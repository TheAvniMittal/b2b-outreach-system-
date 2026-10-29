import io
import os
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import (
    Contact,
    Segment,
    Catalogue,
    ApprovedClaim,
    RulebookConfig,
    AuditLog,
    AuditReason,
    OutboundQueue,
    SendKey,
)
from app.pipeline.catalogue_service import (
    validate_pdf_content,
    save_uploaded_catalogue,
    resolve_send_attachment,
    validate_attachment_text_alignment,
    ensure_catalogues_dir,
)
from app.pipeline.config_service import (
    create_approved_claim,
    upload_claims_csv,
    retire_claim,
    get_active_rulebook,
    update_rulebook,
)
from app.validators.rulebook_checks import validate_rulebook_v0
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.pipeline.sender import execute_send

client = TestClient(app)

SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n164\n%%EOF"
FAKE_PDF_BYTES = b"This is just a text file renamed to fake.pdf with no magic header."

SEGMENT_A = "distributor_wholesale"
SEGMENT_B = "horeca_bakery"


@pytest.fixture(autouse=True)
def clean_phase9_test_data():
    """Cleanup test audit rows, test claims, and test queue items."""
    yield
    with get_db_session() as session:
        # Clean test claims
        session.query(ApprovedClaim).filter(ApprovedClaim.text.like("%[TEST-PHASE9]%")).delete(synchronize_session=False)
        session.commit()


# ---------------------------------------------------------------------------
# 1. PDF Content Validation
# ---------------------------------------------------------------------------

def test_pdf_magic_bytes_validation():
    # Valid PDF with %PDF- header
    valid, err = validate_pdf_content(SAMPLE_PDF_BYTES)
    assert valid is True
    assert err is None

    # Invalid fake PDF
    valid, err = validate_pdf_content(FAKE_PDF_BYTES)
    assert valid is False
    assert "missing %PDF- header" in err

    # Empty file
    valid, err = validate_pdf_content(b"")
    assert valid is False
    assert "empty" in err


# ---------------------------------------------------------------------------
# 2. Catalogue Upload, Storage & Download API
# ---------------------------------------------------------------------------

def test_catalogue_upload_and_download_flow():
    # 1. Upload valid PDF
    files = {"file": ("distributor_test_cat.pdf", io.BytesIO(SAMPLE_PDF_BYTES), "application/pdf")}
    data = {"segment_id": SEGMENT_A, "operator_name": "test_operator"}

    response = client.post("/api/catalogues/upload", files=files, data=data)
    assert response.status_code == 200, response.text
    res_json = response.json()
    assert res_json["success"] is True
    assert res_json["segment_id"] == SEGMENT_A
    assert "checksum" in res_json
    cat_id = res_json["catalogue_id"]

    # Verify audit log
    with get_db_session() as session:
        audit = (
            session.query(AuditLog)
            .filter(AuditLog.reason == AuditReason.CATALOGUE_UPLOADED, AuditLog.object_id == str(cat_id))
            .first()
        )
        assert audit is not None
        assert audit.actor == "test_operator"

    # 2. Download catalogue
    download_res = client.get(f"/api/catalogues/download/{cat_id}")
    assert download_res.status_code == 200
    assert download_res.headers["content-type"] == "application/pdf"
    assert download_res.content == SAMPLE_PDF_BYTES

    # 3. Upload invalid non-PDF file should be rejected with 400
    fake_files = {"file": ("malicious.pdf", io.BytesIO(FAKE_PDF_BYTES), "application/pdf")}
    bad_res = client.post("/api/catalogues/upload", files=fake_files, data=data)
    assert bad_res.status_code == 400
    assert "missing %PDF- header" in bad_res.json()["detail"]


# ---------------------------------------------------------------------------
# 3. Per-Segment Toggle & Attachment Resolution
# ---------------------------------------------------------------------------

def test_segment_catalogue_toggle_and_resolution():
    # 1. Upload a catalogue first
    files = {"file": ("horeca_test.pdf", io.BytesIO(SAMPLE_PDF_BYTES), "application/pdf")}
    client.post("/api/catalogues/upload", files=files, data={"segment_id": SEGMENT_B, "operator_name": "op_lead"})

    with get_db_session() as session:
        contact = session.query(Contact).filter(Contact.segment == SEGMENT_B).first()
        if not contact:
            contact = Contact(
                email="horeca.lead@example.com",
                contact_name="Rajesh Patel",
                company_name="HoReCa Solutions",
                segment=SEGMENT_B,
                status="eligible",
                verification_status="valid",
            )
            session.add(contact)
            session.commit()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            status="eligible",
        )
        session.add(queue_item)
        session.commit()
        qid = queue_item.id

    # 2. Toggle catalogue OFF
    toggle_res = client.post(
        f"/api/segments/{SEGMENT_B}/toggle-catalogue",
        json={"enabled": False, "operator_name": "safety_officer"},
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["attach_catalogue"] is False

    with get_db_session() as session:
        qi = session.query(OutboundQueue).filter(OutboundQueue.id == qid).first()
        att, fallback = resolve_send_attachment(qi, session)
        assert att is None
        assert fallback is None

    # 3. Toggle catalogue ON
    toggle_on_res = client.post(
        f"/api/segments/{SEGMENT_B}/toggle-catalogue",
        json={"enabled": True, "operator_name": "safety_officer"},
    )
    assert toggle_on_res.status_code == 200
    assert toggle_on_res.json()["attach_catalogue"] is True

    with get_db_session() as session:
        qi = session.query(OutboundQueue).filter(OutboundQueue.id == qid).first()
        att, fallback = resolve_send_attachment(qi, session)
        assert att is not None
        assert "Catalogue.pdf" in att["filename"]
        assert att["content"] == SAMPLE_PDF_BYTES
        assert att["checksum"] is not None
        assert fallback is None

        # Clean queue item
        session.delete(qi)
        session.commit()


# ---------------------------------------------------------------------------
# 4. Attachment Text Alignment Verification
# ---------------------------------------------------------------------------

def test_attachment_wording_alignment_gate():
    # Mentions attachment when attachment is present -> Valid
    body_with_mention = "Hi Anil,\n\nPlease find attached our product catalogue for your review.\n\nBest,\nNarayani Team"
    valid, err = validate_attachment_text_alignment(body_with_mention, has_attachment=True)
    assert valid is True
    assert err is None

    # Mentions attachment when attachment is OFF / absent -> Invalid (Hold)
    valid, err = validate_attachment_text_alignment(body_with_mention, has_attachment=False)
    assert valid is False
    assert "promises an attached file" in err
    assert "attachments are turned off or unavailable" in err

    # Does not mention attachment -> Valid regardless of attachment flag
    body_no_mention = "Hi Anil,\n\nWe provide ASTM standard supplies directly to trade partners.\n\nBest,\nNarayani Team"
    valid, err = validate_attachment_text_alignment(body_no_mention, has_attachment=False)
    assert valid is True
    valid, err = validate_attachment_text_alignment(body_no_mention, has_attachment=True)
    assert valid is True


# ---------------------------------------------------------------------------
# 5. Approved Claims Commercial-Term Gate & Soft Retirement
# ---------------------------------------------------------------------------

def test_approved_claims_commercial_term_gate():
    # 1. Price violation -> rejected
    price_claim = {
        "segment": SEGMENT_A,
        "product_category": "Spices",
        "claim_type": "pricing",
        "text": "[TEST-PHASE9] Premium turmeric available at $4.50 per kg with instant delivery.",
        "operator_name": "sales_rep",
    }
    res_price = client.post("/api/claims", json=price_claim)
    assert res_price.status_code == 400
    assert "unapproved commercial terms" in res_price.json()["detail"]

    # 2. MOQ violation -> rejected
    moq_claim = {
        "segment": SEGMENT_A,
        "product_category": "Spices",
        "claim_type": "moq",
        "text": "[TEST-PHASE9] Minimum order quantity is 250 units per batch.",
        "operator_name": "sales_rep",
    }
    res_moq = client.post("/api/claims", json=moq_claim)
    assert res_moq.status_code == 400
    assert "unapproved commercial terms" in res_moq.json()["detail"]

    # 3. Payment terms violation -> rejected
    terms_claim = {
        "segment": SEGMENT_A,
        "product_category": "Terms",
        "claim_type": "payment",
        "text": "[TEST-PHASE9] Standard payment terms are Net 30 days upon receipt.",
        "operator_name": "sales_rep",
    }
    res_terms = client.post("/api/claims", json=terms_claim)
    assert res_terms.status_code == 400
    assert "unapproved commercial terms" in res_terms.json()["detail"]

    # 4. Valid technical claim -> approved and saved
    valid_claim = {
        "segment": SEGMENT_A,
        "product_category": "Spices",
        "claim_type": "specification",
        "text": "[TEST-PHASE9] High-curcumin organic Alleppey turmeric processed in ISO 22000 certified facility.",
        "evidence_note": "FSSAI & Spice Board Certificate #2026-X",
        "operator_name": "sales_engineer",
    }
    res_valid = client.post("/api/claims", json=valid_claim)
    assert res_valid.status_code == 200, res_valid.text
    claim_id = res_valid.json()["claim"]["id"]

    # 5. Retire claim (soft delete)
    retire_res = client.delete(f"/api/claims/{claim_id}?operator_name=compliance_lead")
    assert retire_res.status_code == 200

    # Verify claim still exists in DB but is_active = False
    with get_db_session() as session:
        c = session.query(ApprovedClaim).filter(ApprovedClaim.id == claim_id).first()
        assert c is not None
        assert c.is_active is False

        # Verify audit log
        audit = session.query(AuditLog).filter(
            AuditLog.object_type == "approved_claim",
            AuditLog.object_id == str(claim_id),
            AuditLog.action == "retire_approved_claim",
        ).first()
        assert audit is not None
        assert audit.prev_state == "active"
        assert audit.new_state == "retired"


def test_claims_csv_upload():
    csv_payload = (
        "segment,product_category,claim_type,text,evidence_note\n"
        f"{SEGMENT_B},Bakery Supplies,specification,[TEST-PHASE9] Food-grade cold-pressed virgin coconut oil,Lab MTC 2026\n"
        f"{SEGMENT_B},Pricing,price,[TEST-PHASE9] Available at $6.20/kg for bulk dispatch,Invalid\n"
    )
    res = client.post("/api/claims/upload", json={"csv_content": csv_payload, "operator_name": "data_lead"})
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["created_count"] == 1
    assert len(res_data["errors"]) == 1
    assert "unapproved commercial terms" in res_data["errors"][0]


# ---------------------------------------------------------------------------
# 6. Dynamic Rulebook Management & Validation
# ---------------------------------------------------------------------------

def test_dynamic_rulebook_update_and_validator():
    # 1. Fetch current active rulebook
    get_res = client.get("/api/rulebook")
    assert get_res.status_code == 200
    rb_data = get_res.json()
    current_version = rb_data["version"]

    # 2. Update rulebook with a custom banned phrase
    update_payload = {
        "name": "Updated House Voice vNext",
        "banned_openers": ["hope you are well", "trust all is great"],
        "banned_phrases": ["synergy", "paradigm shift", "revolutionary solution"],
        "max_paragraphs": 3,
        "max_sentences_per_paragraph": 3,
        "operator_name": "lead_editor",
    }
    post_res = client.post("/api/rulebook", json=update_payload)
    assert post_res.status_code == 200
    new_rb = post_res.json()["rulebook"]
    assert new_rb["version"] == current_version + 1
    assert "revolutionary solution" in new_rb["banned_phrases"]

    # 3. Test that validate_rulebook_v0 dynamically catches the new banned phrase
    test_email_body = (
        "Hi Vikram,\n\n"
        "We are pleased to introduce our revolutionary solution for VRF installations in commercial spaces.\n\n"
        "Best regards,\nNarayani Team"
    )
    result = validate_rulebook_v0(body=test_email_body, first_name="Vikram")
    assert result.passed is False
    assert any("revolutionary solution" in err for err in result.errors)


# ---------------------------------------------------------------------------
# 7. Mock Provider & Sender Attachment Logging
# ---------------------------------------------------------------------------

def test_mock_provider_and_sender_attachment():
    with get_db_session() as session:
        # Create a contact and queue item
        contact = session.query(Contact).filter(Contact.segment == SEGMENT_A).first()
        if not contact:
            contact = Contact(
                email="wholesale.boss@example.com",
                contact_name="Amit Shah",
                company_name="Mega Wholesale Corp",
                segment=SEGMENT_A,
                status="eligible",
                verification_status="valid",
            )
            session.add(contact)
            session.commit()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            status="eligible",
        )
        session.add(queue_item)
        session.commit()
        qid = queue_item.id

    mock_provider = MockEmailProvider()

    # Create explicit test attachment
    test_attachments = [
        {
            "filename": "Distributor_Catalogue.pdf",
            "content": SAMPLE_PDF_BYTES,
            "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "size_bytes": len(SAMPLE_PDF_BYTES),
        }
    ]

    with get_db_session() as session:
        send_key, result = execute_send(
            session=session,
            queue_item_id=qid,
            subject="Test Catalogue Delivery",
            body_text="Hi Amit,\n\nPlease find details on our product supplies.\n\nBest,\nNarayani Team",
            provider=mock_provider,
            attachments=test_attachments,
        )

        assert result.success is True
        assert len(mock_provider.sent_messages) == 1
        sent = mock_provider.sent_messages[0]
        assert len(sent["attachments"]) == 1
        assert sent["attachments"][0]["filename"] == "Distributor_Catalogue.pdf"

        # Verify OutboundQueue attachment_checksum was recorded
        qi = session.query(OutboundQueue).filter(OutboundQueue.id == qid).first()
        assert qi.status == "sent"
        assert qi.attachment_checksum == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

        # Cleanup
        session.delete(send_key)
        session.delete(qi)
        session.commit()
