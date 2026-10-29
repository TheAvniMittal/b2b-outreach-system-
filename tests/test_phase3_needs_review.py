import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.pipeline.providers.smtp_provider import SEED_ALLOWLIST

client = TestClient(app)

TEST_EMAIL = "avnimittal900+prospect1@gmail.com"
TEST_SEGMENT = "horeca_bakery"


@pytest.fixture
def setup_test_contact():
    with get_db_session() as session:
        contact = Contact(
            company_name="NeedsReview Bakery Ltd",
            segment=TEST_SEGMENT,
            contact_name="Alice Chef",
            email=TEST_EMAIL,
            verification_status="valid",
            status="pending",
            notes="Initial inquiry note",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    yield contact_id

    with get_db_session() as session:
        session.query(AuditLog).filter(AuditLog.object_id == str(contact_id)).delete(synchronize_session=False)
        session.query(OutboundQueue).filter(OutboundQueue.contact_id == contact_id).delete(synchronize_session=False)
        session.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
        session.commit()


def test_needs_review_items_separation(setup_test_contact):
    contact_id = setup_test_contact

    with get_db_session() as session:
        c = session.query(Contact).filter(Contact.id == contact_id).first()
        c.status = "held"
        c.notes = "Initial note\n[Inbound Reply - wants_catalogue]: Interested in export flour."
        session.commit()

    with get_db_session() as session:
        q = OutboundQueue(
            contact_id=contact_id,
            campaign_step="intro",
            status="held",
            hold_reason="Commercial term violation: price mentioned",
            failed_validator="commercial_term_gate",
            attempts=1,
            rendered_subject="Our Product Range",
            rendered_body="Hello, here are our prices.",
        )
        session.add(q)
        session.commit()
        q_id = q.id

    res = client.get("/api/review/items")
    assert res.status_code == 200, res.text
    data = res.json()

    assert "draft_holds" in data
    assert "contact_replies" in data
    assert "total_review_count" in data

    draft_ids = [d["id"] for d in data["draft_holds"]]
    assert q_id in draft_ids

    reply_contacts = [r for r in data["contact_replies"] if r["id"] == contact_id]
    assert len(reply_contacts) == 1
    assert reply_contacts[0]["inbound_intent"] == "wants_catalogue"
    assert "Interested in export flour" in reply_contacts[0]["notes"]


def test_simulate_inbound_reply_and_operator_reply_flow(setup_test_contact):
    contact_id = setup_test_contact

    sim_res = client.post(
        f"/api/review/contacts/{contact_id}/simulate-reply",
        json={"reply_body": "Hello, we would like to receive your spices catalogue.", "subject": "Product Request"},
    )
    assert sim_res.status_code == 200, sim_res.text
    sim_data = sim_res.json()
    assert sim_data["success"] is True
    assert sim_data["contact_status"] == "held"

    review_res = client.get("/api/review/items")
    assert review_res.status_code == 200
    replies = review_res.json()["contact_replies"]
    held_ids = [r["id"] for r in replies]
    assert contact_id in held_ids

    reply_payload = {
        "operator_name": "Senior Sales Exec",
        "subject": "Re: Spices catalogue request",
        "body": "Hi Alice, attached is our organic spice export catalogue. Let us know if you need sample shipments.",
        "mark_handled": True,
        "notes": "Sent 2026 catalogue overview",
    }
    reply_res = client.post(f"/api/review/contacts/{contact_id}/reply", json=reply_payload)
    assert reply_res.status_code == 200, reply_res.text
    rep_data = reply_res.json()
    assert rep_data["success"] is True
    assert rep_data["contact_status"] == "contacted"
    assert "mock_msg_" in rep_data["message_id"] or rep_data["message_id"]

    review_after = client.get("/api/review/items")
    assert review_after.status_code == 200
    held_ids_after = [r["id"] for r in review_after.json()["contact_replies"]]
    assert contact_id not in held_ids_after

    with get_db_session() as session:
        audit = (
            session.query(AuditLog)
            .filter(AuditLog.object_id == str(contact_id), AuditLog.action == "send_operator_reply")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.OPERATOR_EMAIL_SENT
        assert audit.new_state == "contacted"


def test_mark_reply_handled(setup_test_contact):
    contact_id = setup_test_contact

    with get_db_session() as session:
        c = session.query(Contact).filter(Contact.id == contact_id).first()
        c.status = "held"
        session.commit()

    res = client.post(
        f"/api/review/contacts/{contact_id}/handled",
        json={"operator_name": "Sales Rep", "notes": "Handled via phone discussion"},
    )
    assert res.status_code == 200, res.text

    with get_db_session() as session:
        c = session.query(Contact).filter(Contact.id == contact_id).first()
        assert c.status == "contacted"
        assert "Handled via phone discussion" in c.notes

        audit = (
            session.query(AuditLog)
            .filter(AuditLog.object_id == str(contact_id), AuditLog.action == "reply_handled")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.HUMAN_REPLY


def test_operator_reply_blocked_for_suppressed_contact(setup_test_contact):
    contact_id = setup_test_contact

    with get_db_session() as session:
        c = session.query(Contact).filter(Contact.id == contact_id).first()
        c.status = "suppressed"
        session.commit()

    res = client.post(
        f"/api/review/contacts/{contact_id}/reply",
        json={"operator_name": "Operator", "subject": "Test", "body": "Hello", "mark_handled": True},
    )
    assert res.status_code == 400
    assert "suppressed" in res.json()["detail"].lower()
