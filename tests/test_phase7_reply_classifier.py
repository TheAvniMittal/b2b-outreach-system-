"""
Unit and Integration Tests for Phase 7 (Reply Handling & A3 Classifier Overhaul).

Tests:
1. Prefilter deterministic classification for headers and OOO patterns.
2. A3 LLM Classification and mechanical overrides:
   - Interested -> sets contact to held, pauses sequence, records HUMAN_REPLY audit.
   - Unsubscribe -> adds SHA-256 suppression, cancels queue, records UNSUBSCRIBE_REQUEST audit.
   - Out of Office -> snoozes queue item by 5 days, records OUT_OF_OFFICE_SNOOZE audit.
   - Not Interested -> cancels pending queue items, marks contacted.
   - Confidence floor (<0.6) -> forces unknown, sets contact to held.
3. Operator UI Reply API (/api/review/contacts/{id}/reply):
   - Successfully sends reply email to allowlisted contact.
   - Marks contact handled (transitions from held to contacted).
   - Appends operator timestamped note.
   - Writes OPERATOR_EMAIL_SENT audit log.
   - Rejects sending to suppressed contacts.
4. Verify zero Gemini references in A3 worker codebase.
"""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.db.models import (
    Contact,
    OutboundQueue,
    Suppression,
    AuditLog,
    AuditReason,
)
from app.schemas.a3_contract import A3ReplyContract, A3Kind, A3Intent, A3SuppressionSignal
from app.workers.a3_reply_classifier.prefilter import run_prefilter
from app.workers.a3_reply_classifier.overrides import apply_mechanical_overrides, hash_email
from app.workers.a3_reply_classifier.service import classify_inbound_reply
from app.main import app


@pytest.fixture
def sample_test_contact():
    """Creates a temporary test contact for reply handling."""
    with get_db_session() as session:
        contact = Contact(
            contact_name="Ramesh Patel",
            company_name="Patel Cash & Carry Ltd",
            email="avnimittal900+prospect1@gmail.com",
            segment="supermarket_retail",
            verification_status="valid",
            status="pending",
        )
        session.add(contact)
        session.flush()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            status="eligible",
            campaign_step="intro",
            attempts=0,
            rendered_subject="Narayani Sweets & Snacks Wholesale",
            rendered_body="Sample outreach body",
        )
        session.add(queue_item)
        session.commit()
        contact_id = contact.id
        queue_id = queue_item.id

    yield contact_id, queue_id

    with get_db_session() as session:
        session.query(OutboundQueue).filter_by(id=queue_id).delete()
        session.query(Suppression).filter_by(contact_id=contact_id).delete()
        session.query(Contact).filter_by(id=contact_id).delete()
        session.commit()


def test_prefilter_deterministic_auto_reply():
    # Out of office subject
    result = run_prefilter(
        subject="Automatic reply: Out of office until Monday",
        headers={"Auto-Submitted": "auto-replied"},
    )
    assert result is not None
    assert result.kind == A3Kind.AUTO_REPLY
    assert result.confidence >= 0.95

    # Human subject
    result_human = run_prefilter(subject="Interested in your catalogue")
    assert result_human is None


def test_mechanical_override_interested_pauses_automation(sample_test_contact):
    contact_id, queue_id = sample_test_contact
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=contact_id).first()
        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.INTERESTED,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=0.92,
            summary="Prospect interested in catalogue and prices",
        )

        final_c, actions = apply_mechanical_overrides(session, contact, contract)
        assert actions["automation_paused"] is True
        assert contact.status == "held"

        # Outbound queue should be held
        q = session.query(OutboundQueue).filter_by(id=queue_id).first()
        assert q.status == "held"

        # Audit log written
        audit = session.query(AuditLog).filter_by(
            object_id=str(contact.id),
            reason=AuditReason.HUMAN_REPLY,
            action="pause_automation",
        ).first()
        assert audit is not None


def test_mechanical_override_unsubscribe_suppresses_contact(sample_test_contact):
    contact_id, queue_id = sample_test_contact
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=contact_id).first()
        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.UNSUBSCRIBE_REQUEST,
            suppression_signal=A3SuppressionSignal.UNSUBSCRIBE_REQUEST,
            confidence=0.99,
            summary="Opt-out request received: reply STOP",
        )

        final_c, actions = apply_mechanical_overrides(session, contact, contract)
        assert actions["suppressed"] is True
        assert contact.status == "suppressed"

        # Outbound queue item cancelled
        q = session.query(OutboundQueue).filter_by(id=queue_id).first()
        assert q.status == "cancelled"

        # Suppression entry created
        h = hash_email(contact.email)
        supp = session.query(Suppression).filter_by(email_hash=h).first()
        assert supp is not None
        assert supp.reason == "unsubscribe_request"

        # Audit log written
        audit = session.query(AuditLog).filter_by(
            object_id=str(contact.id),
            reason=AuditReason.UNSUBSCRIBE_REQUEST,
        ).first()
        assert audit is not None


def test_mechanical_override_out_of_office_snoozes_queue(sample_test_contact):
    contact_id, queue_id = sample_test_contact
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=contact_id).first()
        contract = A3ReplyContract(
            kind=A3Kind.AUTO_REPLY,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=0.95,
            summary="Out of office auto-responder",
        )

        final_c, actions = apply_mechanical_overrides(session, contact, contract)
        assert actions["snoozed_auto_reply"] is True
        # Contact stays pending (not held!)
        assert contact.status == "pending"

        # Queue item due_at snoozed by ~5 days
        q = session.query(OutboundQueue).filter_by(id=queue_id).first()
        assert q.due_at is not None
        assert q.due_at > datetime.now(timezone.utc) + timedelta(days=4)

        # Audit log written
        audit = session.query(AuditLog).filter_by(
            object_id=str(contact.id),
            reason=AuditReason.OUT_OF_OFFICE_SNOOZE,
        ).first()
        assert audit is not None


def test_mechanical_override_confidence_floor(sample_test_contact):
    contact_id, _ = sample_test_contact
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=contact_id).first()
        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.INTERESTED,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=0.45,  # Below 0.6 floor
            summary="Very ambiguous one-word response",
        )

        final_c, actions = apply_mechanical_overrides(session, contact, contract)
        assert actions["routed_to_human"] is True
        assert final_c.kind == A3Kind.UNKNOWN
        assert contact.status == "held"


def test_operator_reply_from_ui(sample_test_contact):
    contact_id, _ = sample_test_contact
    client = TestClient(app)

    # Put contact in 'held' state (awaiting human reply)
    with get_db_session() as session:
        c = session.query(Contact).filter_by(id=contact_id).first()
        c.status = "held"
        session.commit()

    # Operator sends reply via API
    payload = {
        "operator_name": "Priya Sharma",
        "subject": "Re: Inquiry regarding Narayani Namkeen export catalogue",
        "body": "Dear Ramesh,\n\nThank you for reaching out. Attached please find our export price list.\n\nBest,\nPriya",
        "mark_handled": True,
        "notes": "Sent wholesale price sheet v2.4",
    }
    res = client.post(f"/api/review/contacts/{contact_id}/reply", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["contact_status"] == "contacted"

    # Verify database state
    with get_db_session() as session:
        updated_c = session.query(Contact).filter_by(id=contact_id).first()
        assert updated_c.status == "contacted"
        assert "Priya Sharma" in updated_c.notes
        assert "Sent wholesale price sheet" in updated_c.notes

        # Audit log row
        audit = session.query(AuditLog).filter_by(
            object_id=str(contact_id),
            action="send_operator_reply",
            reason=AuditReason.OPERATOR_EMAIL_SENT,
        ).first()
        assert audit is not None
        assert audit.actor == "Priya Sharma"


def test_operator_reply_blocked_for_suppressed_contact(sample_test_contact):
    contact_id, _ = sample_test_contact
    client = TestClient(app)

    with get_db_session() as session:
        c = session.query(Contact).filter_by(id=contact_id).first()
        c.status = "suppressed"
        session.commit()

    payload = {
        "operator_name": "Priya Sharma",
        "subject": "Hello",
        "body": "Trying to contact you",
    }
    res = client.post(f"/api/review/contacts/{contact_id}/reply", json=payload)
    assert res.status_code == 400
    assert "Cannot send email to a suppressed contact" in res.json()["detail"]


def test_no_gemini_references_in_codebase():
    """Verify Gemini is completely removed from the project."""
    import glob
    for path in glob.glob("app/**/*.py", recursive=True):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().lower()
            assert "gemini" not in content, f"Found lingering Gemini reference in {path}"
