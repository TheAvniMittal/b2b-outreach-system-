import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.db.session import get_db_session
from app.db.models import (
    Contact,
    OutboundQueue,
    SendKey,
    AuditLog,
    AuditReason,
    SystemControl,
    Suppression,
    record_audit_log,
)
from app.pipeline.queue import (
    enqueue_message,
    claim_next_outbound_task,
    heartbeat,
)
from app.pipeline.sweeper import run_sweeper, get_sweeper_health
from app.core.groq_provider import circuit_breaker, GroqProviderError


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    """Clean queue and related tables before and after each test."""
    with get_db_session() as session:
        session.query(SendKey).delete()
        session.query(AuditLog).delete()
        session.query(OutboundQueue).delete()
        session.query(Suppression).delete()
        session.query(SystemControl).delete()
        session.commit()
    circuit_breaker.reset()
    yield
    with get_db_session() as session:
        session.query(SendKey).delete()
        session.query(AuditLog).delete()
        session.query(OutboundQueue).delete()
        session.query(Suppression).delete()
        session.query(SystemControl).delete()
        session.commit()
    circuit_breaker.reset()


@pytest.fixture
def test_contact_id():
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(verification_status="valid").first()
        if not contact:
            contact = Contact(
                email="director@phase8test.com",
                first_name="Vikram",
                company_name="Phase8 Logistics",
                verification_status="valid",
            )
            session.add(contact)
            session.commit()
        return contact.id


def test_killed_worker_reclaimed_after_lease(test_contact_id):
    """
    Test 1: Kill a worker mid-render -> item is reclaimed after the lease expires.
    Status returns to 'eligible', lease_expiry_count is incremented to 1,
    and draft attempts remains 0.
    """
    base_time = datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id, campaign_step="intro")
        item_id = item.id

        # Worker alpha claims task with lease
        claimed = claim_next_outbound_task(session, worker_id="worker_alpha", lease_seconds=300)
        assert claimed is not None
        assert claimed.id == item_id
        assert claimed.status == "rendering"
        assert claimed.attempts == 0
        assert claimed.lease_expiry_count == 0

    # Worker crashes: no heartbeats. Time advances past lease (350s later)
    sweep_time = base_time + timedelta(seconds=350)
    with get_db_session() as session:
        # Override item lease timestamp to simulate expiry
        row = session.query(OutboundQueue).filter_by(id=item_id).first()
        row.lease_expires_at = base_time + timedelta(seconds=300)
        session.commit()

        # Run recovery sweeper
        result = run_sweeper(session, now_override=sweep_time)
        assert item_id in result["details"]["reclaimed"]

        reclaimed = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert reclaimed.status == "eligible"
        assert reclaimed.locked_by is None
        assert reclaimed.lease_expires_at is None
        assert reclaimed.lease_expiry_count == 1
        assert reclaimed.attempts == 0  # Draft attempts MUST remain 0!

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(item_id), action="reaper_reclaim")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.LEASE_EXPIRED
        assert audit.prev_state == "rendering"
        assert audit.new_state == "eligible"


def test_slow_worker_heartbeat_not_reclaimed(test_contact_id):
    """
    Test 2: A slow worker that keeps making progress via work-loop heartbeats
    is NOT reclaimed before its extended lease expires.
    """
    base_time = datetime(2026, 10, 21, 11, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item_id = item.id
        claimed = claim_next_outbound_task(session, worker_id="worker_active", lease_seconds=300)
        assert claimed is not None

        # At t=200s, work loop sends heartbeat extending lease by 300s
        claimed.lease_expires_at = base_time + timedelta(seconds=500)
        session.commit()

    # At t=250s (past original 300s? No, original was 300s, now 500s), sweeper runs
    sweep_time = base_time + timedelta(seconds=250)
    with get_db_session() as session:
        result = run_sweeper(session, now_override=sweep_time)
        assert item_id not in result["details"]["reclaimed"]

        active_item = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert active_item.status == "rendering"
        assert active_item.locked_by == "worker_active"
        assert active_item.lease_expiry_count == 0


def test_hung_worker_reclaimed_at_hard_max_time(test_contact_id):
    """
    Test 3: A hung worker that continues to heartbeat but exceeds
    MAX_PROCESSING_SECONDS (15 minutes default) is reclaimed anyway.
    """
    base_time = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item_id = item.id
        claimed = claim_next_outbound_task(session, worker_id="worker_hung", lease_seconds=300)
        # Started 16 minutes ago (960 seconds ago)
        claimed.processing_started_at = base_time - timedelta(seconds=960)
        # Heartbeat kept lease fresh into the future
        claimed.lease_expires_at = base_time + timedelta(seconds=200)
        session.commit()

    # Sweeper runs at base_time
    with get_db_session() as session:
        result = run_sweeper(session, now_override=base_time)
        assert item_id in result["details"]["reclaimed"]

        reclaimed = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert reclaimed.status == "eligible"
        assert reclaimed.locked_by is None
        assert reclaimed.lease_expiry_count == 1

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(item_id), action="reaper_reclaim")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.WORKER_CRASH


def test_crashed_sending_reconciled_with_send_key_marked_sent(test_contact_id):
    """
    Test 4a: A worker crashed while sending. Sweeper reconciles with send_key
    and marks it 'sent' when provider confirms delivery. Never sent twice.
    """
    base_time = datetime(2026, 10, 21, 13, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item.status = "sending"
        item.locked_by = "worker_crashed_sending"
        item.lease_expires_at = base_time - timedelta(seconds=30)
        session.flush()

        send_key = SendKey(
            key=f"sendkey_{item.id}",
            queue_item_id=item.id,
            contact_id=test_contact_id,
            status="pending",
            provider_message_id="groq_or_smtp_msg_999",
        )
        session.add(send_key)
        session.commit()
        item_id = item.id

    # Mock reconciliation adapter confirming delivery
    mock_adapter = MagicMock()
    mock_adapter.reconcile.return_value = "sent"

    with get_db_session() as session:
        result = run_sweeper(session, reconciliation_adapter=mock_adapter, now_override=base_time)
        assert item_id in result["details"]["marked_sent"]

        reconciled = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert reconciled.status == "sent"
        assert reconciled.locked_by is None

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(item_id), action="sweeper_reconcile_sent")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.RECONCILE_FOUND


def test_crashed_sending_ambiguous_becomes_held(test_contact_id):
    """
    Test 4b: A crashed sending item cannot be confirmed by provider.
    Sweeper marks it 'held' with the plain reason:
    "We can't tell whether this email was sent. Check your Sent folder."
    """
    base_time = datetime(2026, 10, 21, 14, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item.status = "sending"
        item.locked_by = "worker_crashed_ambiguous"
        item.lease_expires_at = base_time - timedelta(seconds=30)
        session.flush()

        send_key = SendKey(
            key=f"sendkey_{item.id}",
            queue_item_id=item.id,
            contact_id=test_contact_id,
            status="pending",
        )
        session.add(send_key)
        session.commit()
        item_id = item.id

    # Mock reconciliation adapter indicating ambiguous state
    mock_adapter = MagicMock()
    mock_adapter.reconcile.return_value = "unknown"

    with get_db_session() as session:
        result = run_sweeper(session, reconciliation_adapter=mock_adapter, now_override=base_time)
        assert item_id in result["details"]["held"]

        reconciled = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert reconciled.status == "held"
        assert reconciled.hold_reason == "We can't tell whether this email was sent. Check your Sent folder."

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(item_id), action="sweeper_reconcile_ambiguous_hold")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.RECONCILE_AMBIGUOUS_HOLD


def test_three_lease_expiries_lead_to_dead_letter(test_contact_id):
    """
    Test 5: 3 lease expiries / worker crashes lead to status 'dead_letter'.
    Draft attempts remains 0 throughout.
    """
    base_time = datetime(2026, 10, 21, 15, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item_id = item.id

    # Simulate 3 sequential crashes
    for i in range(1, 4):
        with get_db_session() as session:
            claimed = session.query(OutboundQueue).filter_by(id=item_id).first()
            claimed.status = "rendering"
            claimed.locked_by = f"crashed_worker_{i}"
            claimed.lease_expires_at = base_time - timedelta(seconds=10)
            session.commit()

            run_sweeper(session, now_override=base_time)

    with get_db_session() as session:
        final_item = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert final_item.status == "dead_letter"
        assert final_item.locked_by is None
        assert final_item.lease_expiry_count == 3
        assert final_item.attempts == 0  # Still 0 draft attempts!

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(item_id), action="reaper_dead_letter")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.DEAD_LETTER


def test_try_again_on_held_draft_starts_new_round(test_contact_id):
    """
    Test 6: 'Try again' on a held draft with 3 draft attempts does NOT dead-letter it.
    It resets attempts to 0, preserves attempt_history, and returns status to eligible.
    """
    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item.status = "held"
        item.attempts = 3
        item.hold_reason = "Rulebook check failed: Word count 150 > 120"
        item.failed_validator = "rulebook"
        item.attempt_history = [
            {"round": 1, "attempt": 1, "failed_validator": "rulebook", "errors": ["Word count 140"], "timestamp": "2026-10-21T10:00:00Z"},
            {"round": 1, "attempt": 2, "failed_validator": "rulebook", "errors": ["Word count 145"], "timestamp": "2026-10-21T10:01:00Z"},
            {"round": 1, "attempt": 3, "failed_validator": "rulebook", "errors": ["Word count 150"], "timestamp": "2026-10-21T10:02:00Z"},
        ]
        session.commit()
        item_id = item.id

    # Call Retry endpoint
    response = client.post(f"/api/review/{item_id}/retry")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    with get_db_session() as session:
        reloaded = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert reloaded.status == "eligible"
        assert reloaded.attempts == 0
        assert len(reloaded.attempt_history) == 3  # History preserved!


def test_operator_skip_and_do_not_contact_actions(test_contact_id):
    """
    Test 7: Operator actions: Skip person and Mark do-not-contact on held drafts.
    """
    with get_db_session() as session:
        item = enqueue_message(session, contact_id=test_contact_id)
        item.status = "held"
        item.hold_reason = "Formatting error"
        session.commit()
        item_id = item.id

    # 1. Skip person
    resp_skip = client.post(f"/api/review/{item_id}/skip", json={"reason": "Not a current fit"})
    assert resp_skip.status_code == 200
    with get_db_session() as session:
        skipped_item = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert skipped_item.status == "cancelled"
        assert skipped_item.hold_reason == "Not a current fit"

    # Create another held item for Do Not Contact
    with get_db_session() as session:
        item2 = enqueue_message(session, contact_id=test_contact_id)
        item2.status = "held"
        session.commit()
        item2_id = item2.id

    # 2. Do Not Contact
    resp_dnc = client.post(f"/api/review/{item2_id}/do-not-contact", json={"reason": "Requested unsubscribe"})
    assert resp_dnc.status_code == 200
    with get_db_session() as session:
        dnc_item = session.query(OutboundQueue).filter_by(id=item2_id).first()
        assert dnc_item.status == "cancelled"
        assert "do-not-contact" in dnc_item.hold_reason.lower()

        # Verified suppression record was created
        import hashlib
        contact_obj = session.query(Contact).filter_by(id=test_contact_id).first()
        email_hash = hashlib.sha256(contact_obj.email.lower().strip().encode("utf-8")).hexdigest()
        suppression = session.query(Suppression).filter_by(email_hash=email_hash).first()
        assert suppression is not None
        assert suppression.reason == "operator_do_not_contact"


def test_groq_401_circuit_breaker_and_health_reporting():
    """
    Test 8: 401/403 or rejected API key stops immediately without retries,
    trips the circuit breaker, and surfaces groq_key_rejected on System health.
    """
    circuit_breaker.reset()
    is_avail, _ = circuit_breaker.is_available()
    assert is_avail is True
    assert circuit_breaker.is_key_rejected() is False

    # Simulate 401 rejected key
    err = GroqProviderError("Groq authentication failed: 401 Invalid API Key")
    circuit_breaker.record_failure(err)

    assert circuit_breaker.is_key_rejected() is True
    is_avail_after, _ = circuit_breaker.is_available()
    assert is_avail_after is False

    # Verify System Health endpoint returns key rejection
    resp = client.get("/api/system/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["groq_key_rejected"] is True
    assert data["healthy"] is False


def test_empty_sweep_writes_no_audit_row():
    """
    Test 9: An empty sweep (no expired items) writes exactly ZERO audit rows.
    """
    with get_db_session() as session:
        initial_audits = session.query(AuditLog).count()
        res = run_sweeper(session)
        assert res["recovered_count"] == 0
        final_audits = session.query(AuditLog).count()
        assert final_audits == initial_audits


def test_sweeper_runs_while_sending_stopped(test_contact_id):
    """
    Test 10: The sweeper never sends and keeps running when sending is stopped.
    """
    base_time = datetime(2026, 10, 21, 16, 0, 0, tzinfo=timezone.utc)

    with get_db_session() as session:
        # Disable sending via SystemControl
        sys_ctrl = session.query(SystemControl).filter_by(key="sending_allowed").first()
        if not sys_ctrl:
            sys_ctrl = SystemControl(key="sending_allowed", value={"enabled": False})
            session.add(sys_ctrl)
        else:
            sys_ctrl.value = {"enabled": False}
        session.commit()

        # Enqueue and crash a rendering item
        item = enqueue_message(session, contact_id=test_contact_id)
        item.status = "rendering"
        item.locked_by = "worker_offline"
        item.lease_expires_at = base_time - timedelta(seconds=60)
        session.commit()
        item_id = item.id

    # Sweeper must recover it even while sending is stopped
    with get_db_session() as session:
        result = run_sweeper(session, now_override=base_time)
        assert item_id in result["details"]["reclaimed"]
        recovered = session.query(OutboundQueue).filter_by(id=item_id).first()
        assert recovered.status == "eligible"


def test_sweeper_health_reports_stale():
    """
    Test 11: System health page reports sweeper as stale if last_sweep_at
    is older than 3 sweeper intervals (> 180 seconds).
    """
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=200)

    with get_db_session() as session:
        ctrl = session.query(SystemControl).filter_by(key="sweeper_health").first()
        if not ctrl:
            ctrl = SystemControl(key="sweeper_health", value={})
            session.add(ctrl)
        ctrl.value = {
            "last_sweep_at": stale_time.isoformat(),
            "recovered_count": 0,
            "status": "ok",
        }
        session.commit()

        health = get_sweeper_health(session)
        assert health["is_stale"] is True


def test_manual_recovery_button_idempotent():
    """
    Test 12: Manual recovery check button endpoint is idempotent and safe.
    """
    resp1 = client.post("/api/system/recovery/run")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "ok"

    resp2 = client.post("/api/system/recovery/run")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ok"
