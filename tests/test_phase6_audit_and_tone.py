import pytest
import hashlib
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, Suppression, AuditLog, AuditReason
from app.workers.tone_studio.service import parse_groq_studio_response

client = TestClient(app)

TEST_SEGMENT = "horeca_bakery"


def test_tone_studio_parser_handles_raw_newlines_and_markdown():
    # Simulated LLM output with unescaped newlines in JSON string
    raw_llm_output = """```json
{
  "reply_to_operator": "I have adjusted the tone to be warmer and more consultative.",
  "draft_email": "Hi Chef Marco,

We noticed your artisan bakery in Milan has expanded its sourdough line.

Narayani supplies premium sortex-cleaned grains directly from verified mills. Would you be open to reviewing our certified spec sheet?",
  "tone_notes": "Warm, respectful, and consultative.",
  "refused_format_or_price": false
}
```"""
    parsed = parse_groq_studio_response(raw_llm_output)
    assert parsed["reply_to_operator"].startswith("I have adjusted")
    assert "Hi Chef Marco," in parsed["draft_email"]
    assert "artisan bakery" in parsed["draft_email"]
    assert parsed["tone_notes"] == "Warm, respectful, and consultative."
    assert parsed["refused_format_or_price"] is False


def test_tone_studio_parser_handles_prose_fallback():
    raw_prose = "Hi Sarah,\n\nHere is our catalogue of organic pulses.\n\nBest,\nNarayani Team"
    parsed = parse_groq_studio_response(raw_prose)
    assert "draft_email" in parsed
    assert "Hi Sarah," in parsed["draft_email"]
    assert parsed["refused_format_or_price"] is False


def test_audit_log_invalid_reason_does_not_crash():
    # In PostgreSQL, native enum queries with invalid enum values crash with DataError if not guarded
    res = client.get("/api/audit-log?reason=completely_invalid_reason_string")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] == 0
    assert len(data["items"]) == 0


def test_audit_log_valid_reason_filters():
    res = client.get("/api/audit-log?reason=initial_enqueue")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["reason"] == "initial_enqueue"


def test_audit_log_search_filters():
    res = client.get("/api/audit-log?search=enqueue")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] >= 1
    for item in data["items"]:
        match = (
            "enqueue" in item["action"].lower()
            or "enqueue" in item["actor"].lower()
            or "enqueue" in item["object_type"].lower()
            or "enqueue" in item["object_id"].lower()
        )
        assert match, f"Search term 'enqueue' not found in item: {item}"


def test_unsuppress_by_email_hash():
    test_email = "test_phase6_unsupp_hash@example.com"
    e_hash = hashlib.sha256(test_email.strip().lower().encode("utf-8")).hexdigest()

    with get_db_session() as session:
        # Clean any prior test row
        session.query(Suppression).filter(Suppression.email_hash == e_hash).delete(synchronize_session=False)
        contacts = session.query(Contact).filter(Contact.email == test_email).all()
        for c in contacts:
            session.delete(c)
        session.commit()

        # Create contact marked suppressed
        contact = Contact(
            company_name="Phase6 Hash Test Co",
            contact_name="Elena Miller",
            email=test_email,
            segment=TEST_SEGMENT,
            status="suppressed",
            verification_status="valid",
        )
        session.add(contact)
        session.commit()
        cid = contact.id

        # Create suppression record WITHOUT contact_id (hash-only suppression)
        supp = Suppression(
            email_hash=e_hash,
            contact_id=None,
            reason="unsubscribe_request",
            created_by="system",
        )
        session.add(supp)
        session.commit()
        supp_id = supp.id

    try:
        # Call unsuppress
        res = client.post(
            f"/api/suppression/{supp_id}/unsuppress",
            json={"operator_name": "Senior Operator", "reason": "Customer requested re-engagement"}
        )
        assert res.status_code == 200, res.text

        # Verify contact status was restored to pending
        with get_db_session() as session:
            c = session.query(Contact).filter(Contact.id == cid).first()
            assert c is not None
            assert c.status == "pending"

            # Verify suppression was deleted
            s = session.query(Suppression).filter(Suppression.id == supp_id).first()
            assert s is None
    finally:
        with get_db_session() as session:
            session.query(Suppression).filter(Suppression.email_hash == e_hash).delete(synchronize_session=False)
            session.query(Contact).filter(Contact.id == cid).delete(synchronize_session=False)
            session.commit()
