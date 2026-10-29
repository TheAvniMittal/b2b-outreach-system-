import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import get_db_session
from app.db.models import OutboundQueue, Contact, Segment, Catalogue, ApprovedClaim
from app.pipeline.reason_formatter import (
    format_hold_reason,
    format_error_detail,
    format_attempt_history,
    clean_raw_error_message,
)
from app.db.seed import seed_all


client = TestClient(app)


def test_reason_formatter_all_categories():
    """Verifies that all validator error patterns are correctly translated into plain language."""

    # 1. Catalogue Grounding (banned product terms from other catalogues e.g. 'snacks')
    res = format_hold_reason(
        hold_reason="Check (h) failed: Check (h2) Grounding violation: email mentions banned product terms from other catalogues: 'snacks' (belongs to 'dry_fruits' catalogue)...",
        failed_validator="catalogue_grounding",
        attempts=3,
    )
    assert res["plain_label"] == "Held (3 of 3 attempts)"
    assert "snacks" in res["plain_reason"].lower()
    assert "not approved for this customer type" in res["plain_reason"]
    assert "(3 of 3 attempts)" in res["plain_reason"]
    assert res["failed_validator_label"] == "Catalogue Grounding"
    assert "Edit & Approve" in res["action_hint"]

    # 2. Rulebook word count
    res_wc = format_hold_reason(
        hold_reason="Word count 42 is outside the allowed range [80, 120].",
        failed_validator="rulebook",
        attempts=2,
    )
    assert res_wc["plain_label"] == "Held (2 of 3 attempts)"
    assert "42 words" in res_wc["plain_reason"]
    assert "target of 80 to 120 words" in res_wc["plain_reason"]
    assert res_wc["failed_validator_label"] == "Word Count Range"

    # 3. Greeting format
    res_greet = format_hold_reason(
        hold_reason="Line 1 greeting must be exactly 'Hi Vikram,', got 'Hello there'.",
        failed_validator="rulebook",
        attempts=1,
    )
    assert "must be 'Hi <FirstName>,'" in res_greet["plain_reason"]
    assert res_greet["failed_validator_label"] == "Greeting Format"

    # 4. Banned phrases
    res_banned = format_hold_reason(
        hold_reason="Contains banned phrase: 'reaching out'.",
        failed_validator="rulebook",
        attempts=3,
    )
    assert "Contains banned phrase 'reaching out'" in res_banned["plain_reason"]

    # 5. Commercial terms
    res_comm = format_hold_reason(
        hold_reason="Check (i) failed: commercial terms detected: ['Commercial term $4.50 adjacent to numeric']",
        failed_validator="commercial_terms",
        attempts=3,
    )
    assert "commercial terms, pricing, or minimum order quantities" in res_comm["plain_reason"]
    assert res_comm["failed_validator_label"] == "Commercial Term Gate"

    # 6. Tone sample reference near-copy
    res_tone = format_hold_reason(
        hold_reason="Tone reference copy violation: similarity 0.62 >= 0.50 to Tone Studio approved sample.",
        failed_validator="sameness",
        attempts=3,
    )
    assert "Tone Studio approved sample" in res_tone["plain_reason"]
    assert res_tone["failed_validator_label"] == "Sameness & Repetition"

    # 7. Veto skip
    res_veto = format_hold_reason(
        hold_reason="this business does not fit the segment",
        failed_validator="veto",
        status="vetoed",
    )
    assert res_veto["is_veto"] is True
    assert res_veto["plain_label"] == "Skipped (Veto)"
    assert res_veto["plain_reason"] == "Skipped (veto): this business does not fit the segment"
    assert res_veto["failed_validator_label"] == "Prospect Fit Veto"

    # 8. AI service busy (Groq 429 / timeout)
    res_ai = format_hold_reason(
        hold_reason="AI service unavailable: 429 Too Many Requests rate limit",
        status="waiting_for_ai",
    )
    assert res_ai["is_paused"] is True
    assert res_ai["plain_label"] == "Paused (AI Busy)"
    assert res_ai["plain_reason"] == "Paused: Groq was busy, will retry automatically"
    assert res_ai["failed_validator_label"] == "AI Service Availability"


def test_no_raw_codes_leaked_in_formatting():
    """Assures raw codes are never presented to human operator."""
    raw_codes = ["VALIDATOR_HOLD", "exhausted_repairs", "check_h2", "check_h", "check_i", "check_g", "h1 failed"]
    for code in raw_codes:
        res = format_hold_reason(f"System encountered {code} during processing", attempts=3)
        assert "VALIDATOR_HOLD" not in res["plain_reason"]
        assert "exhausted_repairs" not in res["plain_reason"]
        assert "check_h2" not in res["plain_reason"]


def test_attempt_history_formatting():
    """Tests that raw attempt history records are formatted with human-friendly labels and error lists."""
    raw_history = [
        {
            "attempt": 1,
            "failed_validator": "rulebook",
            "errors": ["Line 1 greeting must be exactly 'Hi Aarav,', got 'Hey'."],
        },
        {
            "attempt": 2,
            "failed_validator": "catalogue_grounding",
            "errors": ["Check (h) failed: email mentions banned product terms from other catalogues: 'snacks'."],
        },
        {
            "attempt": 3,
            "failed_validator": "commercial_terms",
            "errors": ["Commercial term '$500' adjacent to numeric."],
        },
    ]

    formatted = format_attempt_history(raw_history)
    assert len(formatted) == 3

    assert formatted[0]["attempt"] == 1
    assert formatted[0]["failed_validator_label"] == "Greeting Format"
    assert "Hi <FirstName>," in formatted[0]["errors"][0]

    assert formatted[1]["attempt"] == 2
    assert formatted[1]["failed_validator_label"] == "Catalogue Grounding"
    assert "snacks" in formatted[1]["errors"][0]

    assert formatted[2]["attempt"] == 3
    assert formatted[2]["failed_validator_label"] == "Commercial Term Gate"


def test_api_review_items_returns_plain_fields():
    """Verifies /api/review/items and /api/review/drafts/{id} endpoints return plain language fields."""
    with get_db_session() as db:
        # Create a test contact and held queue item with attempt history
        contact = Contact(
            company_name="Phase5 Test Corp",
            contact_name="Aarav Test",
            email="aarav.test@phase5example.com",
            segment="importer_india",
            verification_status="valid",
            status="pending",
        )
        db.add(contact)
        db.flush()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            campaign_step="intro",
            status="held",
            attempts=3,
            hold_reason="Check (h) failed: email mentions banned product terms from other catalogues: 'snacks' (belongs to 'dry_fruits' catalogue)",
            failed_validator="catalogue_grounding",
            attempt_history=[
                {"attempt": 1, "failed_validator": "rulebook", "errors": ["Word count 40 is outside the allowed range [80, 120]."]},
                {"attempt": 2, "failed_validator": "rulebook", "errors": ["Word count 50 is outside the allowed range [80, 120]."]},
                {"attempt": 3, "failed_validator": "catalogue_grounding", "errors": ["email mentions banned product terms from other catalogues: 'snacks'"]},
            ],
            rendered_subject="Narayani Spices for Phase 5",
            rendered_body="Hi Aarav,\n\nTest body snacks.\n\nBest regards,\nAarav Shah",
        )
        db.add(queue_item)
        db.commit()
        q_id = queue_item.id
        c_id = contact.id

    try:
        # 1. Test /api/review/items
        res = client.get("/api/review/items")
        assert res.status_code == 200
        data = res.json()
        target = next((d for d in data["draft_holds"] if d["id"] == q_id), None)
        assert target is not None
        assert target["plain_label"] == "Held (3 of 3 attempts)"
        assert "snacks" in target["plain_hold_reason"].lower()
        assert target["failed_validator_label"] == "Catalogue Grounding"
        assert "action_hint" in target and len(target["action_hint"]) > 0
        assert len(target["attempt_history"]) == 3
        assert target["attempt_history"][0]["attempt"] == 1
        assert target["is_veto"] is False

        # 2. Test /api/review/drafts/{draft_id}
        res_detail = client.get(f"/api/review/drafts/{q_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["id"] == q_id
        assert detail["plain_label"] == "Held (3 of 3 attempts)"
        assert detail["failed_validator_label"] == "Catalogue Grounding"
        assert len(detail["attempt_history"]) == 3

        # 3. Test /api/queue includes formatted fields
        res_q = client.get("/api/queue?status=held")
        assert res_q.status_code == 200
        q_data = res_q.json()
        q_target = next((item for item in q_data["items"] if item["id"] == q_id), None)
        assert q_target is not None
        assert q_target["plain_label"] == "Held (3 of 3 attempts)"
        assert "snacks" in q_target["plain_hold_reason"].lower()
        assert q_target["action_hint"] is not None
        assert len(q_target["attempt_history"]) == 3

    finally:
        with get_db_session() as db:
            db.query(OutboundQueue).filter(OutboundQueue.id == q_id).delete()
            db.query(Contact).filter(Contact.id == c_id).delete()
            db.commit()


def test_seed_insert_only_if_missing():
    """Verifies that seed_all does not overwrite existing records."""
    with get_db_session() as db:
        # Fetch or insert segment importer_india
        seg = db.query(Segment).filter_by(segment="importer_india").first()
        if not seg:
            seed_all(db)
            seg = db.query(Segment).filter_by(segment="importer_india").first()

        orig_register = seg.register_spec
        # Modify register_spec locally to test non-overwriting
        seg.register_spec = "Custom Operator Spec That Must Not Be Overwritten"
        db.commit()

        # Re-run seed_all
        seed_all(db)

        # Verify it was NOT overwritten
        seg_refreshed = db.query(Segment).filter_by(segment="importer_india").first()
        assert seg_refreshed.register_spec == "Custom Operator Spec That Must Not Be Overwritten"

        # Restore original
        seg_refreshed.register_spec = orig_register
        db.commit()
