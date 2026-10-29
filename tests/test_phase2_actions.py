"""
Tests for Phase 2 Human Operator Actions & Safety Gates.
Verifies validation on edited drafts, retry/dismiss flows, reply takeovers, un-suppression, and CSV imports.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.pipeline.suppression_gate import hash_email

client = TestClient(app)


def test_approve_draft_validation_rejection_exclamation():
    """Confirms drafts containing exclamation marks are rejected with HTTP 422."""
    with get_db_session() as session:
        c = Contact(
            company_name="Acme Corp",
            contact_name="Aarav Sharma",
            email="aarav@acme-test.example",
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="held",
            hold_reason="validator_hold",
        )
        session.add(q)
        session.commit()
        q_id = q.id
        c_id = c.id

    try:
        # Invalid body with exclamation mark
        payload = {
            "operator_name": "Test Operator",
            "subject": "Supply Inquiry",
            "body": "Hi Aarav,\n\nWe provide certified spices and dry fruits directly to wholesale distributors across North India. Our processing maintains high standards of batch consistency and purity.\n\nWould you like to review our catalogue!\n\nBest regards,\nAarav Shah",
        }
        res = client.post(f"/api/review/{q_id}/approve", json=payload)
        assert res.status_code == 422
        data = res.json()
        assert data["passed"] is False
        assert any("exclamation" in err.lower() for err in data["errors"])

        # Confirm queue item remains held in DB
        with get_db_session() as session:
            refreshed_q = session.query(OutboundQueue).filter(OutboundQueue.id == q_id).first()
            assert refreshed_q.status == "held"

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_approve_draft_validation_rejection_commercial_term():
    """Confirms drafts with commercial terms (e.g. pricing or MOQ) are rejected with HTTP 422."""
    with get_db_session() as session:
        c = Contact(
            company_name="Acme Corp",
            contact_name="Aarav Sharma",
            email="aarav2@acme-test.example",
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="held",
            hold_reason="validator_hold",
        )
        session.add(q)
        session.commit()
        q_id = q.id
        c_id = c.id

    try:
        # Invalid body with pricing commitment ($20 per kg)
        payload = {
            "operator_name": "Test Operator",
            "subject": "Supply Pricing at $20",
            "body": "Hi Aarav,\n\nWe provide certified spices and dry fruits directly to wholesale distributors across North India. We offer $20 per kg for container shipments.\n\nWould you like to review our catalogue?\n\nBest regards,\nAarav Shah",
        }
        res = client.post(f"/api/review/{q_id}/approve", json=payload)
        assert res.status_code == 422
        data = res.json()
        assert data["passed"] is False
        assert len(data["errors"]) > 0

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_approve_draft_success_flow():
    """Confirms clean draft passes validation, updates status to eligible, and logs audit record."""
    with get_db_session() as session:
        c = Contact(
            company_name="Acme Corp",
            contact_name="Aarav Sharma",
            email="aarav3@acme-test.example",
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="held",
            hold_reason="validator_hold",
        )
        session.add(q)
        session.commit()
        q_id = q.id
        c_id = c.id

    try:
        # Valid body (100-150 words for importer_india, proper greeting, 2 sentences per paragraph, no exclamations or commercial terms)
        body_words = [
            "Hi Aarav,",
            "",
            "Narayani Distributors supplies certified whole spices and dry fruits to established importers and distributors across North India. Our regional packing facilities maintain complete batch traceability and direct origin sourcing for bulk staples.",
            "",
            "We coordinate supply schedules to support regional distribution channels with consistent order fulfilment across every seasonal cycle. Our team manages batch testing to ensure consistent standard compliance across all deliveries. In addition, we assist procurement partners with tailored scheduling to align with inventory timelines.",
            "",
            "We would be glad to share our current product specifications and trade documentation for your procurement needs. Would you be open to reviewing our product catalogue this week?",
            "",
            "Best regards,",
            "Aarav Shah",
        ]
        clean_body = "\n".join(body_words)

        payload = {
            "operator_name": "Senior Operator",
            "subject": "Narayani Distributors / Wholesale Supply Introduction",
            "body": clean_body,
        }
        res = client.post(f"/api/review/{q_id}/approve", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["passed"] is True

        # Verify DB state
        with get_db_session() as session:
            refreshed_q = session.query(OutboundQueue).filter(OutboundQueue.id == q_id).first()
            assert refreshed_q.status == "eligible"
            assert refreshed_q.hold_reason is None

            # Verify AuditLog
            audit = (
                session.query(AuditLog)
                .filter(AuditLog.object_id == str(q_id), AuditLog.action == "approve_draft")
                .first()
            )
            assert audit is not None
            assert audit.actor == "Senior Operator"
            assert audit.prev_state == "held"
            assert audit.new_state == "eligible"

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE object_id = :qid"), {"qid": str(q_id)})
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_retry_held_draft():
    """Confirms retry clears hold reason and returns draft to eligible status."""
    with get_db_session() as session:
        c = Contact(
            company_name="Retry Test Corp",
            contact_name="Deepak Joshi",
            email="deepak@retry-test.example",
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="held",
            hold_reason="temporary_network_hold",
            attempts=1,
        )
        session.add(q)
        session.commit()
        q_id = q.id
        c_id = c.id

    try:
        res = client.post(f"/api/review/{q_id}/retry", json={"operator_name": "Ops Lead"})
        assert res.status_code == 200

        with get_db_session() as session:
            refreshed_q = session.query(OutboundQueue).filter(OutboundQueue.id == q_id).first()
            assert refreshed_q.status == "eligible"
            assert refreshed_q.hold_reason is None
            assert refreshed_q.attempts == 0

            audit = (
                session.query(AuditLog)
                .filter(AuditLog.object_id == str(q_id), AuditLog.action == "retry_draft")
                .first()
            )
            assert audit is not None
            assert audit.actor == "Ops Lead"

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE object_id = :qid"), {"qid": str(q_id)})
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_dismiss_held_draft():
    """Confirms dismiss transitions queue item to cancelled with audit log."""
    with get_db_session() as session:
        c = Contact(
            company_name="Dismiss Test Corp",
            contact_name="Karan Malhotra",
            email="karan@dismiss-test.example",
            segment="importer_india",
            status="pending",
            verification_status="valid",
        )
        session.add(c)
        session.flush()

        q = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="held",
            hold_reason="veto_limit",
        )
        session.add(q)
        session.commit()
        q_id = q.id
        c_id = c.id

    try:
        res = client.post(
            f"/api/review/{q_id}/dismiss",
            json={"operator_name": "Supervisor", "reason": "unviable_angle"},
        )
        assert res.status_code == 200

        with get_db_session() as session:
            refreshed_q = session.query(OutboundQueue).filter(OutboundQueue.id == q_id).first()
            assert refreshed_q.status == "cancelled"

            audit = (
                session.query(AuditLog)
                .filter(AuditLog.object_id == str(q_id), AuditLog.action == "dismiss_draft")
                .first()
            )
            assert audit is not None
            assert audit.actor == "Supervisor"
            assert audit.prev_state == "held"
            assert audit.new_state == "cancelled"

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE object_id = :qid"), {"qid": str(q_id)})
            session.execute(text("DELETE FROM outbound_queue WHERE id = :qid"), {"qid": q_id})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_mark_reply_handled():
    """Confirms inbound reply contact can be marked handled and returned to contacted status."""
    with get_db_session() as session:
        c = Contact(
            company_name="Reply Test Corp",
            contact_name="Pooja Verma",
            email="pooja@reply-test.example",
            segment="importer_india",
            status="held",
            notes="Interested in bulk cardamom.",
        )
        session.add(c)
        session.commit()
        c_id = c.id

    try:
        res = client.post(
            f"/api/review/contacts/{c_id}/handled",
            json={"operator_name": "Account Exec", "notes": "Called client; sent price sheet."},
        )
        assert res.status_code == 200

        with get_db_session() as session:
            refreshed_c = session.query(Contact).filter(Contact.id == c_id).first()
            assert refreshed_c.status == "contacted"
            assert "Called client; sent price sheet." in refreshed_c.notes

            audit = (
                session.query(AuditLog)
                .filter(AuditLog.object_id == str(c_id), AuditLog.action == "reply_handled")
                .first()
            )
            assert audit is not None
            assert audit.actor == "Account Exec"
            assert audit.reason == AuditReason.HUMAN_REPLY

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE object_id = :cid"), {"cid": str(c_id)})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_unsuppress_contact_strict_requirements():
    """Confirms un-suppression requires name & reason, deletes row, and writes UNSUPPRESSION audit log."""
    test_email = "unsuppress_test@example.com"
    email_hash = hash_email(test_email)

    with get_db_session() as session:
        c = Contact(
            company_name="Suppressed Corp",
            contact_name="Sunil Shah",
            email=test_email,
            segment="importer_india",
            status="suppressed",
        )
        session.add(c)
        session.flush()

        s = Suppression(
            email_hash=email_hash,
            contact_id=c.id,
            reason="accidental_optout",
            created_by="system",
        )
        session.add(s)
        session.commit()
        s_id = s.id
        c_id = c.id

    try:
        # 1. Missing operator or reason fails with 400
        res_fail = client.post(f"/api/suppression/{s_id}/unsuppress", json={"operator_name": "", "reason": ""})
        assert res_fail.status_code == 400

        # 2. Valid unsuppress request
        res = client.post(
            f"/api/suppression/{s_id}/unsuppress",
            json={
                "operator_name": "Compliance Director",
                "reason": "Customer called support requesting to rejoin the outreach list.",
            },
        )
        assert res.status_code == 200

        with get_db_session() as session:
            # Row must be deleted
            supp_check = session.query(Suppression).filter(Suppression.id == s_id).first()
            assert supp_check is None

            # Contact status restored to pending
            c_check = session.query(Contact).filter(Contact.id == c_id).first()
            assert c_check.status == "pending"

            # Mandatory UNSUPPRESSION audit row (§14.8b)
            audit = (
                session.query(AuditLog)
                .filter(AuditLog.object_id == str(s_id), AuditLog.action == "unsuppress")
                .first()
            )
            assert audit is not None
            assert audit.actor == "Compliance Director"
            assert audit.reason == AuditReason.UNSUPPRESSION

    finally:
        with get_db_session() as session:
            session.execute(text("DELETE FROM audit_log WHERE object_id = :sid"), {"sid": str(s_id)})
            session.execute(text("DELETE FROM contacts WHERE id = :cid"), {"cid": c_id})
            session.commit()


def test_import_csv_endpoint():
    """Confirms CSV import via API inserts verified contacts and returns structured report."""
    csv_content = """company_name,contact_name,email,segment,country
Test Importer 1,Ravi Patel,ravi.patel.import@test-domain.example,importer_india,India
Test Importer 2,Sanjay Mehta,sanjay.mehta.import@test-domain.example,importer_india,India
"""
    res = client.post("/api/contacts/import-csv", json={"csv_text": csv_content})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["total_rows"] == 2
    assert data["inserted_contacts"] == 2

    # Cleanup
    with get_db_session() as session:
        session.execute(text("DELETE FROM audit_log WHERE object_id LIKE '%import%'"))
        session.execute(text("DELETE FROM contacts WHERE email LIKE '%@test-domain.example'"))
        session.commit()
