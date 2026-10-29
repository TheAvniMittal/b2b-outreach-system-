"""
Unit and Integration Tests for Phase 6 (Sending Controls & Reconciled Numbers).

Tests:
1. is_sending_active logic (active, stopped, paused segment, auto-stop triggered).
2. set_sending_state and audit logging (content-free, exact reason enums).
3. Auto-stop threshold enforcement:
   - > 5% hard bounce rate requires at least 10 sends.
   - Policy / spam block triggers auto-stop immediately.
   - Spam complaint triggers auto-stop immediately.
4. Clear auto-stop functionality and audit logging.
5. Reconciled numbers panel mathematical invariants:
   - held_for_review == awaiting_approval + replies_waiting
   - remaining_today == max(0, today_allowed - sent_today)
6. Pipeline runner pre-claim and pre-send guards.
7. Dashboard API endpoints (/api/dashboard/sending/toggle, /numbers, etc.).
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.db.models import (
    SystemControl,
    Contact,
    OutboundQueue,
    SendKey,
    AuditLog,
    Suppression,
    AuditReason,
)
from app.pipeline.sending_controller import (
    is_sending_active,
    set_sending_state,
    set_segment_pause,
    check_auto_stop_conditions,
    clear_auto_stop,
    get_reconciled_numbers,
    KEY_SENDING_ACTIVE,
    KEY_PAUSED_SEGMENTS,
    KEY_AUTO_STOP,
)
from app.pipeline.runner import run_single_pipeline_item
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.main import app


@pytest.fixture(autouse=True)
def clean_system_controls():
    """Ensure system_controls has clean default active state before each test."""
    with get_db_session() as session:
        session.query(SystemControl).filter(
            SystemControl.key.in_([KEY_SENDING_ACTIVE, KEY_PAUSED_SEGMENTS, KEY_AUTO_STOP])
        ).delete(synchronize_session=False)
        session.commit()
    yield
    with get_db_session() as session:
        session.query(SystemControl).filter(
            SystemControl.key.in_([KEY_SENDING_ACTIVE, KEY_PAUSED_SEGMENTS, KEY_AUTO_STOP])
        ).delete(synchronize_session=False)
        session.commit()


def test_sending_toggle_and_audit():
    with get_db_session() as session:
        # Initially active by default
        active, reason = is_sending_active(session)
        assert active is True
        assert reason is None

        # Stop sending
        res = set_sending_state(session, active=False, operator_name="Lead Op")
        assert res["is_sending_active"] is False

        active, reason = is_sending_active(session)
        assert active is False
        assert "stopped by operator" in reason.lower()

        # Verify audit log
        audit = session.query(AuditLog).filter_by(
            action="stop_sending",
            reason=AuditReason.SENDING_STOPPED
        ).order_by(AuditLog.id.desc()).first()
        assert audit is not None
        assert audit.actor == "Lead Op"

        # Resume sending
        res = set_sending_state(session, active=True, operator_name="Lead Op")
        assert res["is_sending_active"] is True

        active, reason = is_sending_active(session)
        assert active is True

        # Verify resume audit log
        audit_start = session.query(AuditLog).filter_by(
            action="start_sending",
            reason=AuditReason.SENDING_STARTED
        ).order_by(AuditLog.id.desc()).first()
        assert audit_start is not None
        assert audit_start.actor == "Lead Op"


def test_segment_pause():
    with get_db_session() as session:
        # Pause HoReCa segment
        res = set_segment_pause(session, segment="horeca", paused=True, operator_name="Op Seg")
        assert "horeca" in res["paused_segments"]

        # Retail should still be active
        act_retail, _ = is_sending_active(session, segment="retail")
        assert act_retail is True

        # HoReCa should be paused
        act_horeca, reason = is_sending_active(session, segment="horeca")
        assert act_horeca is False
        assert "horeca" in reason.lower()

        # Unpause HoReCa
        res = set_segment_pause(session, segment="horeca", paused=False, operator_name="Op Seg")
        assert "horeca" not in res["paused_segments"]

        act_horeca, _ = is_sending_active(session, segment="horeca")
        assert act_horeca is True


def test_auto_stop_bounce_threshold_requires_min_sends():
    """
    Test that > 5% hard bounces only triggers auto-stop when sends >= min_sends (10).
    """
    with get_db_session() as session:
        # Under 10 sends (e.g. 5 sent, 1 bounce = 20% > 5%) -> does NOT trigger
        triggered, msg = check_auto_stop_conditions(
            session=session,
            override_sent_today=5,
            override_hard_bounces=1,
            override_policy_blocks=0,
            override_spam_complaints=0,
        )
        assert triggered is False
        assert msg is None

        # At or above 10 sends (e.g. 10 sent, 1 bounce = 10% > 5%) -> DOES trigger
        triggered, msg = check_auto_stop_conditions(
            session=session,
            override_sent_today=10,
            override_hard_bounces=1,
            override_policy_blocks=0,
            override_spam_complaints=0,
        )
        assert triggered is True
        assert "Hard bounces exceeded 5% threshold" in msg

        # Pipeline is now auto-stopped
        act, r = is_sending_active(session)
        assert act is False
        assert "auto-stop safety triggered" in r.lower()

        # Clear auto-stop
        res = clear_auto_stop(session, operator_name="Deliverability Admin", notes="Bounced address removed")
        assert res["auto_stop_triggered"] is False

        act, _ = is_sending_active(session)
        assert act is True


def test_auto_stop_policy_block_and_complaints():
    """
    Test that any policy block or spam complaint triggers auto-stop immediately.
    """
    with get_db_session() as session:
        # Policy block
        triggered, msg = check_auto_stop_conditions(
            session=session,
            override_sent_today=2,
            override_hard_bounces=0,
            override_policy_blocks=1,
            override_spam_complaints=0,
        )
        assert triggered is True
        assert "Email provider policy/spam block detected" in msg

        clear_auto_stop(session, operator_name="Admin")

        # Spam complaint
        triggered, msg = check_auto_stop_conditions(
            session=session,
            override_sent_today=2,
            override_hard_bounces=0,
            override_policy_blocks=0,
            override_spam_complaints=1,
        )
        assert triggered is True
        assert "Spam complaint received" in msg


def test_reconciled_numbers_panel():
    with get_db_session() as session:
        numbers = get_reconciled_numbers(session)

        # Invariant 1: held_for_review == awaiting_approval + replies_waiting
        assert numbers["held_for_review"] == numbers["awaiting_approval"] + numbers["replies_waiting"]

        # Invariant 2: remaining_today == max(0, today_allowed - sent_today)
        assert numbers["remaining_today"] == max(0, numbers["today_allowed"] - numbers["sent_today"])

        # Reconciled flag must be True
        assert numbers["reconciled"] is True


def test_runner_pre_claim_and_pre_send_blocks():
    """
    Verify that when sending is stopped, process_next_outbound_task returns False
    and does not claim or send any queue items.
    """
    with get_db_session() as session:
        set_sending_state(session, active=False, operator_name="Test Operator")

        # Calling runner while sending is stopped should return sending_stopped immediately
        provider = MockEmailProvider()
        result = run_single_pipeline_item(session=session, worker_id="test_worker_1", provider=provider)
        assert result is not None
        assert result.get("status") == "sending_stopped"

        # Restore
        set_sending_state(session, active=True, operator_name="Test Operator")


def test_dashboard_api_endpoints():
    client = TestClient(app)

    # 1. GET /api/dashboard
    res = client.get("/api/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "reconciled_numbers" in data
    assert "sending_control" in data
    assert "environment_banner" in data
    assert data["reconciled_numbers"]["reconciled"] is True
    assert data["environment_banner"]["allowlist_count"] > 0

    # 2. POST /api/dashboard/sending/toggle
    res = client.post("/api/dashboard/sending/toggle", json={"active": False, "operator_name": "API Op"})
    assert res.status_code == 200
    assert res.json()["sending_active"] is False

    # Check status
    res = client.get("/api/dashboard/sending/status")
    assert res.status_code == 200
    assert res.json()["is_sending_active"] is False

    # Resume via API
    res = client.post("/api/dashboard/sending/toggle", json={"active": True, "operator_name": "API Op"})
    assert res.status_code == 200
    assert res.json()["sending_active"] is True

    # 3. GET /api/dashboard/numbers
    res = client.get("/api/dashboard/numbers")
    assert res.status_code == 200
    nums = res.json()
    assert nums["held_for_review"] == nums["awaiting_approval"] + nums["replies_waiting"]
