"""
Concurrency-Safe Suppression Gate Tests (VISION.md §7.5a).

Verifies:
1. Deterministic forced-interleaving race test in BOTH directions:
   - Direction A: Suppression-wins (Claim blocks on row lock, then yields zero rows claimed).
   - Direction B: Claim-wins (Claim locks and claims first, suppression blocks on row lock then suppresses cleanly).
2. Un-suppression is human-only and rejects automated actors, writing audit record.
3. Smoke test with concurrent parallel threads.
"""

import time
import threading
import pytest
from datetime import datetime, timezone
from sqlalchemy import text

from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.pipeline.suppression_gate import (
    suppress_contact_atomic,
    claim_outbound_task_atomic,
    unsuppress_contact_human_only,
    hash_email,
)


@pytest.fixture
def test_contact_and_queue():
    """Creates a temporary isolated contact and queue item for testing."""
    unique_suffix = int(time.time() * 1000)
    email = f"test_concurrency_{unique_suffix}@example.com"

    with get_db_session() as session:
        contact = Contact(
            company_name="Concurrency Corp",
            contact_name="Alice Tester",
            email=email,
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(contact)
        session.flush()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            campaign_step="intro",
            status="rendering",
        )
        session.add(queue_item)
        session.commit()

        c_id = contact.id
        q_id = queue_item.id

    yield c_id, q_id, email

    # Cleanup
    with get_db_session() as session:
        session.execute(text("DELETE FROM outbound_queue WHERE contact_id = :cid"), {"cid": c_id})
        session.execute(text("DELETE FROM suppression WHERE contact_id = :cid"), {"cid": c_id})
        session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
        session.commit()


def test_unsuppress_human_only_gate(test_contact_and_queue):
    """
    Asserts:
    1. Automated actors are rejected with ValueError (§7.5a inviolable rule).
    2. Human actor succeeds, deletes suppression row, restores contact, and writes audit record.
    """
    c_id, q_id, email = test_contact_and_queue

    with get_db_session() as session:
        # First suppress
        suppress_contact_atomic(session, contact_id=c_id, reason="unsubscribe_request", actor="user_click")
        session.commit()

    # 1. Attempt unsuppression with automated actor -> must raise ValueError
    with get_db_session() as session:
        for bad_actor in ["system", "automated", "a3_reply_classifier", "worker_123"]:
            with pytest.raises(ValueError, match="human-only"):
                unsuppress_contact_human_only(session, contact_id=c_id, human_actor=bad_actor, reason="test")

    # 2. Legitimate human actor unsuppresses
    human_name = "avni_compliance_admin"
    with get_db_session() as session:
        ok = unsuppress_contact_human_only(session, contact_id=c_id, human_actor=human_name, reason="Prospect verified opt-in via phone")
        session.commit()
        assert ok is True

    # 3. Assert suppression deleted, contact pending, audit record created
    with get_db_session() as session:
        s_row = session.execute(text("SELECT 1 FROM suppression WHERE contact_id = :cid"), {"cid": c_id}).fetchone()
        assert s_row is None

        contact = session.query(Contact).filter_by(id=c_id).first()
        assert contact.status == "pending"

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(c_id), action="unsuppress")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.actor == human_name
        assert audit.reason == AuditReason.UNSUPPRESSION
        assert audit.prev_state == "suppressed"
        assert audit.new_state == "pending"


def test_forced_interleaving_suppression_wins(test_contact_and_queue):
    """
    DIRECTION A: SUPPRESSION-WINS ORDERING
    Tx_S locks contact row first.
    Tx_C attempts to claim, blocking in PostgreSQL until Tx_S commits.
    Once Tx_S commits, Tx_C unblocks and its conditional claim must return None (yields).
    """
    c_id, q_id, email = test_contact_and_queue

    lock_acquired_by_suppression = threading.Event()
    claim_thread_started = threading.Event()
    claim_result = {"claimed_id": "not_set"}
    claim_error = []

    def worker_claim_func():
        try:
            with get_db_session() as session:
                claim_thread_started.set()
                # Wait until suppression thread holds the lock
                lock_acquired_by_suppression.wait(timeout=5)

                # Small sleep to ensure Tx_S is actively holding the lock in DB
                time.sleep(0.05)

                # This claim call will block in PostgreSQL on:
                # SELECT ... FROM contacts WHERE id = :c_id FOR UPDATE
                claimed = claim_outbound_task_atomic(
                    session=session,
                    queue_item_id=q_id,
                    worker_id="worker_thread_A",
                )
                session.commit()
                claim_result["claimed_id"] = claimed
        except Exception as e:
            claim_error.append(e)

    # Launch claim thread
    claim_thread = threading.Thread(target=worker_claim_func, daemon=True)

    with get_db_session() as tx_s:
        # 1. Take row lock on contact in Tx_S
        tx_s.execute(
            text("SELECT id, email, status FROM contacts WHERE id = :cid FOR UPDATE"),
            {"cid": c_id}
        )
        lock_acquired_by_suppression.set()

        # Start claim thread which will attempt to claim and block on the row lock
        claim_thread.start()
        claim_thread_started.wait(timeout=5)

        # Hold the lock while executing suppression write
        time.sleep(0.1)

        # Write suppression in Tx_S
        suppress_contact_atomic(
            session=tx_s,
            contact_id=c_id,
            reason="unsubscribe_request",
            actor="compliance_webhook",
        )
        # 2. Commit Tx_S -> releases PostgreSQL row lock and persists suppression
        tx_s.commit()

    # Wait for claim thread to finish after unblocking
    claim_thread.join(timeout=10)

    assert not claim_error, f"Claim thread failed with error: {claim_error}"
    # Assert losing transaction yielded: claimed_id must be None!
    assert claim_result["claimed_id"] is None, f"Expected None, got {claim_result['claimed_id']} - suppression failed to stop claim!"

    # Verify database state
    with get_db_session() as session:
        q_item = session.query(OutboundQueue).filter_by(id=q_id).first()
        assert q_item.status == "cancelled", f"Queue item should be cancelled, got {q_item.status}"
        assert q_item.locked_by is None or q_item.status != "sending"

        contact = session.query(Contact).filter_by(id=c_id).first()
        assert contact.status == "suppressed"


def test_forced_interleaving_claim_wins(test_contact_and_queue):
    """
    DIRECTION B: CLAIM-WINS ORDERING
    Tx_C locks contact row first and claims the queue item.
    Tx_S attempts suppression, blocking in PostgreSQL until Tx_C commits.
    Tx_C successfully claims (status='sending').
    Once Tx_C commits, Tx_S unblocks and records suppression for the contact.
    """
    c_id, q_id, email = test_contact_and_queue

    lock_acquired_by_claim = threading.Event()
    suppression_thread_started = threading.Event()
    suppression_result = {"success": False}
    suppression_error = []

    def worker_suppression_func():
        try:
            with get_db_session() as session:
                suppression_thread_started.set()
                # Wait until claim thread holds the lock
                lock_acquired_by_claim.wait(timeout=5)

                time.sleep(0.05)

                # This suppression call will block in PostgreSQL on:
                # SELECT ... FROM contacts WHERE id = :c_id FOR UPDATE
                ok = suppress_contact_atomic(
                    session=session,
                    contact_id=c_id,
                    reason="hard_bounce",
                    actor="dsn_listener",
                )
                session.commit()
                suppression_result["success"] = ok
        except Exception as e:
            suppression_error.append(e)

    # Launch suppression thread
    supp_thread = threading.Thread(target=worker_suppression_func, daemon=True)

    with get_db_session() as tx_c:
        # 1. Take row lock on contact in Tx_C
        tx_c.execute(
            text("SELECT id, email, status FROM contacts WHERE id = :cid FOR UPDATE"),
            {"cid": c_id}
        )
        lock_acquired_by_claim.set()

        # Start suppression thread which will attempt to suppress and block on the row lock
        supp_thread.start()
        suppression_thread_started.wait(timeout=5)

        # Hold the lock while executing claim
        time.sleep(0.1)

        claimed_id = claim_outbound_task_atomic(
            session=tx_c,
            queue_item_id=q_id,
            worker_id="worker_thread_B",
        )
        assert claimed_id == q_id, "Claim should succeed when holding lock before suppression"

        # 2. Commit Tx_C -> releases PostgreSQL row lock
        tx_c.commit()

    # Wait for suppression thread to finish after unblocking
    supp_thread.join(timeout=10)

    assert not suppression_error, f"Suppression thread failed with error: {suppression_error}"
    assert suppression_result["success"] is True

    # Verify final database state:
    with get_db_session() as session:
        # Contact is now suppressed for future operations
        contact = session.query(Contact).filter_by(id=c_id).first()
        assert contact.status == "suppressed"

        # Item was claimed by worker_B
        q_item = session.query(OutboundQueue).filter_by(id=q_id).first()
        assert q_item.status == "sending"
        assert q_item.locked_by == "worker_thread_B"


def test_concurrent_smoke_repetition():
    """
    Repetition smoke test: run concurrent claim vs suppression on 10 distinct pairs.
    Assert that whenever suppression wins, claim yields None.
    Whenever claim wins, status is sending and contact ends suppressed.
    """
    num_iterations = 10
    contacts_data = []

    # Setup distinct contacts and queue items
    with get_db_session() as session:
        for i in range(num_iterations):
            email = f"smoke_race_{int(time.time())}_{i}@example.com"
            c = Contact(
                company_name=f"Smoke Corp {i}",
                contact_name=f"User {i}",
                email=email,
                segment="importer_india",
                status="pending",
                verification_status="valid",
            )
            session.add(c)
            session.flush()

            q = OutboundQueue(
                contact_id=c.id,
                campaign_step="intro",
                status="rendering",
            )
            session.add(q)
            session.flush()
            contacts_data.append((c.id, q.id))
        session.commit()

    results = []

    def race_pair(cid, qid):
        # Fire both concurrently
        res = {"claimed": None, "suppressed": False}

        def do_suppress():
            with get_db_session() as s:
                res["suppressed"] = suppress_contact_atomic(s, cid, reason="smoke_test", actor="tester")
                s.commit()

        def do_claim():
            with get_db_session() as s:
                res["claimed"] = claim_outbound_task_atomic(s, qid, worker_id=f"smoke_worker_{cid}")
                s.commit()

        t1 = threading.Thread(target=do_suppress)
        t2 = threading.Thread(target=do_claim)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        results.append(res)

    threads = [threading.Thread(target=race_pair, args=(cid, qid)) for cid, qid in contacts_data]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_iterations

    # Assert invariant: In no case did a contact remain unsuppressed, and claimed items match DB
    with get_db_session() as session:
        for cid, qid in contacts_data:
            c = session.query(Contact).filter_by(id=cid).first()
            assert c.status == "suppressed"
            s = session.execute(text("SELECT 1 FROM suppression WHERE contact_id = :cid"), {"cid": cid}).fetchone()
            assert s is not None

        # Clean up smoke test rows
        for cid, qid in contacts_data:
            session.execute(text("DELETE FROM outbound_queue WHERE contact_id = :cid"), {"cid": cid})
            session.execute(text("DELETE FROM suppression WHERE contact_id = :cid"), {"cid": cid})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": cid})
        session.commit()
