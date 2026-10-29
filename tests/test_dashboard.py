"""
Tests for Dashboard API Router (Stage 7, VISION.md §18.3).
"""

import time
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.pipeline.suppression_gate import hash_email


client = TestClient(app)


def test_dashboard_ui_html_serving():
    """Confirms GET /dashboard-ui serves the HTML visual dashboard."""
    response = client.get("/dashboard-ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    text = response.text
    assert "Narayani Outreach System" in text
    assert "chart.js" in text.lower()
    assert "queueChart" in text
    assert "fetchDashboardData" in text
    assert "countdown" in text


def test_dashboard_endpoint_structure():
    """Confirms GET /api/dashboard returns HTTP 200 and matches required contract."""
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()

    # 1. Contacts by verification status
    assert "contacts_by_verification_status" in data
    assert isinstance(data["contacts_by_verification_status"], dict)
    assert "valid" in data["contacts_by_verification_status"]

    # 2. Outbound queue by status
    assert "outbound_queue_by_status" in data
    assert isinstance(data["outbound_queue_by_status"], dict)
    for st in ["eligible", "rendering", "sending", "sent", "held", "dead_letter"]:
        assert st in data["outbound_queue_by_status"]

    # 3. Vetoes and holds from today
    assert "vetoes_and_holds_today" in data
    assert "total" in data["vetoes_and_holds_today"]
    assert "validator_holds" in data["vetoes_and_holds_today"]
    assert "veto_limits" in data["vetoes_and_holds_today"]
    assert "contacts_held" in data["vetoes_and_holds_today"]

    # 4. Suppressions and their reasons (enums only, content-free design)
    assert "suppressions" in data
    assert "total" in data["suppressions"]
    assert "by_reason" in data["suppressions"]
    assert isinstance(data["suppressions"]["by_reason"], dict)

    # 5. Sent count for today vs daily cap
    assert "daily_sends" in data
    assert "sent_today" in data["daily_sends"]
    assert "daily_cap" in data["daily_sends"]
    assert "remaining_allowance" in data["daily_sends"]
    assert "cap_reached" in data["daily_sends"]


def test_dashboard_reflects_database_changes():
    """
    Inserts controlled test entities and verifies dashboard reflects the exact delta.
    """
    # Baseline
    r_initial = client.get("/api/dashboard").json()
    initial_valid_contacts = r_initial["contacts_by_verification_status"].get("valid", 0)
    initial_eligible_queue = r_initial["outbound_queue_by_status"].get("eligible", 0)
    initial_suppressions = r_initial["suppressions"]["total"]

    unique_ts = int(time.time() * 1000)
    test_email = f"dashboard_test_{unique_ts}@example.com"

    with get_db_session() as session:
        # Create 1 valid contact
        c = Contact(
            company_name="Dashboard Test Corp",
            contact_name="Bob Test",
            email=test_email,
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        # Create 1 eligible queue row
        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="eligible",
        )
        session.add(q)

        # Create 1 suppression row with reason 'hard_bounce'
        s = Suppression(
            email_hash=hash_email(test_email),
            contact_id=c.id,
            reason="hard_bounce",
            created_by="test_suite",
        )
        session.add(s)

        # Record 1 validator hold audit row
        a = AuditLog(
            actor="validator_system",
            action="validator_hold",
            object_type="queue_item",
            object_id=str(q.id),
            reason=AuditReason.VALIDATOR_HOLD,
        )
        session.add(a)
        session.commit()

        c_id = c.id
        q_id = q.id
        a_id = a.id

    try:
        # Re-query dashboard
        r_after = client.get("/api/dashboard").json()

        # Assert increments
        assert r_after["contacts_by_verification_status"]["valid"] == initial_valid_contacts + 1
        assert r_after["outbound_queue_by_status"]["eligible"] == initial_eligible_queue + 1
        assert r_after["suppressions"]["total"] == initial_suppressions + 1
        assert "hard_bounce" in r_after["suppressions"]["by_reason"]
        assert r_after["vetoes_and_holds_today"]["validator_holds"] >= 1

    finally:
        # Cleanup test entities
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE id = :aid"), {"aid": a_id})
            session.execute(text("DELETE FROM suppression WHERE contact_id = :cid"), {"cid": c_id})
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()
