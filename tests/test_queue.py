from datetime import datetime, timedelta, timezone
import pytest
from freezegun import freeze_time

from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, AuditLog, AuditReason
from app.pipeline.queue import (
    enqueue_message,
    claim_next_outbound_task,
    heartbeat,
    run_reaper,
    MAX_ATTEMPTS_BEFORE_DEAD_LETTER,
)

@pytest.fixture(autouse=True)
def clean_queue():
    """Clean queue before each test to ensure test isolation."""
    with get_db_session() as session:
        session.query(OutboundQueue).delete()
        session.commit()
    yield
    with get_db_session() as session:
        session.query(OutboundQueue).delete()
        session.commit()


@pytest.fixture
def test_contact_id():
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(verification_status="valid").first()
        assert contact is not None, "A valid contact must exist for queue tests"
        return contact.id


def test_claim_lease_reaper_recovery_with_freezegun(test_contact_id):
    """
    Test §14.1b:
    1. Enqueue message
    2. Worker A claims row -> status='rendering', lease_expires_at = now + 90s
    3. Jump time past 90s (e.g. 100s) via freezegun simulating worker A died/wedged
    4. Run reaper
    5. Assert row is reclaimed to 'eligible', attempts == 1, not stuck!
    """
    start_time = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

    with freeze_time(start_time) as frozen_time:
        with get_db_session() as session:
            item = enqueue_message(session, contact_id=test_contact_id, campaign_step="intro")
            item_id = item.id

            # Worker claims the row
            claimed = claim_next_outbound_task(session, worker_id="worker_alpha", lease_seconds=90)
            assert claimed is not None
            assert claimed.id == item_id
            assert claimed.status == "rendering"
            assert claimed.locked_by == "worker_alpha"
            assert claimed.lease_expires_at == start_time + timedelta(seconds=90)

        # Advance time by 100 seconds (past 90s lease)
        frozen_time.tick(delta=timedelta(seconds=100))

        # Run reaper
        with get_db_session() as session:
            reap_results = run_reaper(session)
            assert item_id in reap_results["reclaimed"]

            # Verify row state
            reclaimed_item = session.query(OutboundQueue).filter_by(id=item_id).first()
            assert reclaimed_item.status == "eligible"
            assert reclaimed_item.locked_by is None
            assert reclaimed_item.lease_expires_at is None
            assert reclaimed_item.lease_expiry_count == 1
            assert reclaimed_item.attempts == 0

            # Verify audit entry
            audit_entry = (
                session.query(AuditLog)
                .filter_by(object_id=str(item_id), action="reaper_reclaim")
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert audit_entry is not None
            assert audit_entry.reason == AuditReason.LEASE_EXPIRED
            assert audit_entry.new_state == "eligible"


def test_three_failed_attempts_transition_to_dead_letter(test_contact_id):
    """
    Test §14.1b:
    3 failed attempts -> DEAD_LETTER for human review.
    """
    start_time = datetime(2026, 9, 21, 14, 0, 0, tzinfo=timezone.utc)

    with freeze_time(start_time) as frozen_time:
        with get_db_session() as session:
            item = enqueue_message(session, contact_id=test_contact_id, campaign_step="intro")
            item_id = item.id

        # Loop through attempts
        for attempt_idx in range(1, MAX_ATTEMPTS_BEFORE_DEAD_LETTER + 1):
            with get_db_session() as session:
                claimed = (
                    session.query(OutboundQueue)
                    .filter_by(id=item_id, status="eligible")
                    .first()
                )
                if claimed:
                    claimed.status = "rendering"
                    claimed.locked_by = f"worker_attempt_{attempt_idx}"
                    claimed.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=90)
                    session.commit()

            # Advance past lease expiry
            frozen_time.tick(delta=timedelta(seconds=100))

            # Run reaper
            with get_db_session() as session:
                run_reaper(session)

        # After 3 attempts, row must be in dead_letter
        with get_db_session() as session:
            final_item = session.query(OutboundQueue).filter_by(id=item_id).first()
            assert final_item.status == "dead_letter"
            assert final_item.locked_by is None
            assert final_item.lease_expiry_count == 3
            assert final_item.attempts == 0

            dead_letter_audit = (
                session.query(AuditLog)
                .filter_by(object_id=str(item_id), action="reaper_dead_letter")
                .first()
            )
            assert dead_letter_audit is not None
            assert dead_letter_audit.reason == AuditReason.DEAD_LETTER


def test_heartbeat_extends_lease(test_contact_id):
    """
    Test §14.1b:
    Worker actively sends heartbeat every 20s, extending lease so reaper does not steal it.
    """
    start_time = datetime(2026, 9, 21, 16, 0, 0, tzinfo=timezone.utc)

    with freeze_time(start_time) as frozen_time:
        with get_db_session() as session:
            item = enqueue_message(session, contact_id=test_contact_id)
            item_id = item.id
            claimed = claim_next_outbound_task(session, worker_id="worker_active", lease_seconds=90)
            assert claimed is not None
            assert claimed.id == item_id

        # At t=80s, send heartbeat extending lease by 90s (new expiry = 80 + 90 = 170s)
        frozen_time.tick(delta=timedelta(seconds=80))
        with get_db_session() as session:
            ok = heartbeat(session, queue_item_id=item_id, worker_id="worker_active", extend_seconds=90)
            assert ok is True

        # At t=100s, run reaper. Initial 90s would have expired, but heartbeat extended to 170s!
        frozen_time.tick(delta=timedelta(seconds=20))
        with get_db_session() as session:
            reap_results = run_reaper(session)
            assert item_id not in reap_results["reclaimed"]

            active_item = session.query(OutboundQueue).filter_by(id=item_id).first()
            assert active_item.status == "rendering"
            assert active_item.locked_by == "worker_active"
