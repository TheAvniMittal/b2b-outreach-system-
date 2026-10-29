"""
Phase 2 Tone Studio Backend Tests (§A).
Verifies:
1. 10-per-day generation limit per segment.
2. Tone-only guardrails and format/pricing refusals.
3. Validation checklist on every sample and re-check at approval.
4. Approval workflow: versioning, archiving, content-free audit_log row (AuditReason.TONE_APPROVED).
5. Rejection workflow: recording rejected samples with reasons.
6. Rollback workflow: restoring earlier version, audit_log row (AuditReason.TONE_ROLLBACK).
7. Synthetic contact insulation (real prospect data never accessed).
8. "What the Email Writer will receive" payload.
9. FastAPI endpoints integration.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db_session
from app.db.models import (
    Segment,
    ToneReference,
    ToneStudioSession,
    AuditLog,
    AuditReason,
    AIUsageLog,
)
from app.workers.tone_studio.synthetic_contacts import get_synthetic_contact
from app.workers.tone_studio.service import (
    get_daily_studio_usage,
    validate_sample_email,
    generate_tone_sample,
    approve_tone_reference,
    reject_tone_sample,
    rollback_tone_reference,
    get_writer_preview_payload,
)

client = TestClient(app)


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def test_synthetic_contact_isolation():
    """Verify that synthetic contact data is isolated and does not use real prospect records."""
    contact = get_synthetic_contact("distributor_wholesale")
    assert contact["is_synthetic"] is True
    assert "Apex Wholesale" in contact["company_name"]
    assert "Priya Sharma" in contact["contact_name"]
    assert contact["segment"] == "distributor_wholesale"

    # Dynamic fallback for arbitrary segment
    dyn_contact = get_synthetic_contact("custom_niche_segment")
    assert dyn_contact["is_synthetic"] is True
    assert dyn_contact["segment"] == "custom_niche_segment"


def test_plain_language_validation_checklist(db: Session):
    """Verify that validate_sample_email executes plain-language checks."""
    seg = db.query(Segment).filter(Segment.segment == "distributor_wholesale").first()
    assert seg is not None

    # Valid draft (approx 100 words, within 90-130 range, professional, no forbidden pricing)
    valid_draft = (
        "Hi Priya,\n\n"
        "We noticed Apex Wholesale's active expansion across regional FMCG supply chains in western India. "
        "Narayani Distributors provides certified, high-turnover grocery lines and packaged staples specifically "
        "tailored for wholesale distribution networks. Our logistics ensure batch consistency, direct territory exclusivity "
        "options, and reliable fulfillment schedules. We partner closely with established regional distributors to maximize stock "
        "turnover and margin predictability while reducing operational overhead. In addition, our documentation meets all state "
        "and national regulatory compliance standards to guarantee seamless dispatch.\n\n"
        "Would you be open to reviewing our wholesale product catalogue and trade terms this week?\n\n"
        "Best regards,"
    )

    res = validate_sample_email(valid_draft, seg)
    assert res["all_passed"] is True
    assert len(res["checklist"]) >= 3
    for item in res["checklist"]:
        assert item["passed"] is True
        assert isinstance(item["plain_reason"], str)

    # Draft with commercial pricing violation
    bad_pricing_draft = (
        "Hi Priya,\n\n"
        "We are offering a special 25% discount and 90 days credit on all bulk purchases! "
        "Everything costs $5 per unit. We guarantee the cheapest prices in the entire market."
    )
    res_bad = validate_sample_email(bad_pricing_draft, seg)
    assert res_bad["all_passed"] is False
    # Check that commercial terms item caught it
    comm_item = next(i for i in res_bad["checklist"] if i["name"] == "Commercial Terms")
    assert comm_item["passed"] is False


def test_daily_generation_limit_enforcement(db: Session):
    """Verify that generation enforces 10/day per segment cap from ai_usage_log."""
    test_seg = "distributor_wholesale"

    # Seed 10 successful usage logs for today
    seeded_logs = []
    for _ in range(10):
        entry = AIUsageLog(
            agent_name="tone_studio",
            model_name="llama-3.1-8b-instant",
            call_type=f"studio_generation_{test_seg}",
            tokens_prompt=500,
            tokens_completion=150,
            total_tokens=650,
            status="success",
        )
        db.add(entry)
        seeded_logs.append(entry)
    db.commit()

    usage = get_daily_studio_usage(db, test_seg)
    assert usage["used_today"] >= 10
    assert usage["remaining_today"] == 0

    # 11th call must be blocked with plain message
    with pytest.raises(ValueError) as exc:
        generate_tone_sample(
            session=db,
            segment=test_seg,
            operator_instruction="Make it warmer",
            operator_name="Aarav",
        )
    assert "Daily generation limit reached" in str(exc.value)

    # Cleanup seeded usage logs
    for entry in seeded_logs:
        db.delete(entry)
    db.commit()


def test_generate_sample_with_mocked_groq(db: Session):
    """Verify generate_tone_sample handles Groq response and logs usage."""
    test_seg = "horeca_bakery"

    mock_llm_json = {
        "reply_to_operator": "Adjusted the tone to be more consultative and focused on kitchen reliability.",
        "draft_email": (
            "Hi Chef Vikram,\n\n"
            "We have followed Baker's Choice commercial kitchen operations and dedication to premium pastry production. "
            "Narayani provides commercial bakeries with certified, batch-consistent flours, specialty sugars, and commercial "
            "kitchen staples engineered for steady production output. Our direct supply prevents variance between production "
            "batches and includes full lot traceability so your team maintains consistent texture and flavor profiles. "
            "We coordinate scheduled weekly logistics to ensure your storage and prep cycles are never interrupted.\n\n"
            "Would you be open to receiving a dedicated ingredient sample pack for your kitchen testing?\n\n"
            "Best regards,"
        ),
        "tone_notes": "Warm, respectful of culinary craft, consultative tone.",
        "refused_format_or_price": False,
    }

    mock_response = MagicMock()
    mock_response.content = json.dumps(mock_llm_json)
    mock_response.response_metadata = {
        "token_usage": {"prompt_tokens": 400, "completion_tokens": 120}
    }

    with patch("app.workers.tone_studio.service.get_groq_chat") as mock_get_chat:
        mock_chat = MagicMock()
        mock_chat.invoke.return_value = mock_response
        mock_get_chat.return_value = mock_chat

        result = generate_tone_sample(
            session=db,
            segment=test_seg,
            operator_instruction="Make it warmer and respectful of culinary craft",
            operator_name="Operator Sarah",
        )

        assert "Chef Vikram" in result["draft_email"]
        assert result["refused_format_or_price"] is False
        assert "validation" in result
        assert result["synthetic_contact"]["contact_name"] == "Chef Vikram Rao"

    # Cleanup created AIUsageLog
    latest_log = (
        db.query(AIUsageLog)
        .filter(AIUsageLog.agent_name == "tone_studio", AIUsageLog.call_type == f"studio_generation_{test_seg}")
        .order_by(AIUsageLog.created_at.desc())
        .first()
    )
    if latest_log:
        db.delete(latest_log)
        db.commit()


def test_approve_tone_reference_workflow(db: Session):
    """Verify approval creates active ToneReference, increments version, archives previous, and logs audit."""
    test_seg = "supermarket_retail"
    operator = "Lead Reviewer"

    valid_draft_v1 = (
        "Hi Sanjay,\n\n"
        "We noted Prime Mart's growing retail footprint across urban grocery markets and consumer centers. "
        "Narayani supplies high-velocity packaged food lines and staples with certified barcoding, "
        "transparent lot tracking, and verified retail shelf-life. Our packaging is engineered for rapid "
        "shelf placement, immediate visual recognition, and consistent inventory rotation. We work directly "
        "with supermarket merchandising teams to provide dependable weekly dispatch cycles and planned promotional "
        "support to keep out-of-stock rates minimal.\n\n"
        "Could we share our full retail category deck and sample formats for your procurement team to evaluate?\n\n"
        "Best regards,"
    )
    notes_v1 = "Professional and retail-focused tone."

    # Approve v1
    v1 = approve_tone_reference(
        session=db,
        segment=test_seg,
        draft_body=valid_draft_v1,
        tone_notes=notes_v1,
        operator_name=operator,
    )

    assert v1.is_active is True
    assert v1.status == "approved"
    assert v1.version_number >= 1

    # Verify audit log
    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.object_type == "tone_reference", AuditLog.object_id == str(v1.id))
        .first()
    )
    assert audit_row is not None
    assert audit_row.actor == operator
    assert audit_row.reason == AuditReason.TONE_APPROVED
    assert audit_row.new_state == f"v{v1.version_number}"

    # Now approve v2
    valid_draft_v2 = (
        "Hello Sanjay,\n\n"
        "Following Prime Mart's recent supermarket expansion across primary regional retail locations, "
        "Narayani provides certified FMCG and packaged food lines tailored for shelf-ready merchandising. "
        "Our products feature standard retail EAN barcoding, verified batch consistency, and reliable consumer appeal. "
        "We maintain disciplined dispatch timelines and dedicated account coordination so your distribution centers "
        "and store shelves experience uninterrupted replenishment cycles. We also provide complete marketing collateral "
        "and introductory point-of-sale display assets to ensure strong customer pickup from day one.\n\n"
        "Would you be open to reviewing our formal listing proposal and category terms this week?\n\n"
        "Warm regards,"
    )
    notes_v2 = "Direct, relationship-driven retail tone."

    v2 = approve_tone_reference(
        session=db,
        segment=test_seg,
        draft_body=valid_draft_v2,
        tone_notes=notes_v2,
        operator_name=operator,
    )

    assert v2.is_active is True
    assert v2.version_number == v1.version_number + 1

    # Check v1 is archived
    db.refresh(v1)
    assert v1.is_active is False
    assert v1.status == "archived"

    # Cleanup
    db.delete(v2)
    db.delete(v1)
    if audit_row:
        db.delete(audit_row)
    db.commit()


def test_approve_blocked_if_draft_fails_validation(db: Session):
    """Verify that approval is rejected if the draft fails validators."""
    test_seg = "supermarket_retail"
    bad_draft = "Short text with 50% discount and credit terms."

    with pytest.raises(ValueError) as exc:
        approve_tone_reference(
            session=db,
            segment=test_seg,
            draft_body=bad_draft,
            tone_notes="Discount tone",
            operator_name="Operator Jane",
        )
    assert "Cannot approve" in str(exc.value)


def test_rollback_tone_reference(db: Session):
    """Verify rollback restores previous version, deactivates active version, and writes audit log."""
    test_seg = "organic_gourmet"
    operator = "Rollback Specialist"

    # Create v1 and v2
    v1_ref = ToneReference(
        segment=test_seg,
        version_number=1,
        draft_body="Hi Ananya,\nSample draft v1 for organic segment with clean label.",
        tone_notes="Original organic tone.",
        status="archived",
        is_active=False,
        actor=operator,
    )
    v2_ref = ToneReference(
        segment=test_seg,
        version_number=2,
        draft_body="Hi Ananya,\nSample draft v2 for organic segment with provenance emphasis.",
        tone_notes="Second organic tone.",
        status="approved",
        is_active=True,
        actor=operator,
    )
    db.add(v1_ref)
    db.add(v2_ref)
    db.commit()

    # Roll back to v1
    rolled_back = rollback_tone_reference(
        session=db,
        segment=test_seg,
        version_number=1,
        operator_name=operator,
    )

    assert rolled_back.id == v1_ref.id
    assert rolled_back.is_active is True
    assert rolled_back.status == "approved"

    db.refresh(v2_ref)
    assert v2_ref.is_active is False
    assert v2_ref.status == "archived"

    # Verify audit log
    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.object_type == "tone_reference", AuditLog.object_id == str(v1_ref.id), AuditLog.action == "tone_rollback")
        .first()
    )
    assert audit_row is not None
    assert audit_row.actor == operator
    assert audit_row.reason == AuditReason.TONE_ROLLBACK
    assert audit_row.new_state == "v1"

    # Cleanup
    db.delete(v2_ref)
    db.delete(v1_ref)
    if audit_row:
        db.delete(audit_row)
    db.commit()


def test_reject_tone_sample(db: Session):
    """Verify rejected tone samples are recorded with optional reason and not set to active."""
    test_seg = "wellness_gym"
    rej = reject_tone_sample(
        session=db,
        segment=test_seg,
        draft_body="Draft that felt too aggressive.",
        reason="Felt too pushy for gym owners.",
        operator_name="Operator Mark",
    )

    assert rej.status == "rejected"
    assert rej.is_active is False
    assert rej.rejection_reason == "Felt too pushy for gym owners."

    # Cleanup
    db.delete(rej)
    db.commit()


def test_writer_preview_payload(db: Session):
    """Verify 'What the Email Writer will receive' payload structure."""
    test_seg = "distributor_wholesale"
    preview = get_writer_preview_payload(db, test_seg)
    assert "status" in preview
    assert "tone_notes" in preview
    assert "sample_excerpt" in preview


def test_api_endpoints_integration():
    """Verify Tone Studio API endpoints through FastAPI TestClient."""
    # 1. GET /api/tone-studio/segments
    res = client.get("/api/tone-studio/segments")
    assert res.status_code == 200
    data = res.json()
    assert "segments" in data
    seg_names = [s["segment"] for s in data["segments"]]
    assert "distributor_wholesale" in seg_names
    assert "horeca_bakery" in seg_names

    # 2. GET /api/tone-studio/distributor_wholesale/chat
    chat_res = client.get("/api/tone-studio/distributor_wholesale/chat")
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data["segment"] == "distributor_wholesale"
    assert "synthetic_contact" in chat_data
    assert "writer_preview" in chat_data
    assert "quick_prompts" in chat_data
    assert len(chat_data["quick_prompts"]) == 5

    # 3. Validation rejection on approve with missing operator name
    app_res = client.post(
        "/api/tone-studio/distributor_wholesale/approve",
        json={"draft_body": "Too short", "tone_notes": "test", "operator_name": ""},
    )
    assert app_res.status_code == 422  # validation error (operator_name min_length=1)
