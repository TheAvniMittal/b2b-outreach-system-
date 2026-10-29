import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.db.models import Contact, Segment, Catalogue, ApprovedClaim, OutboundQueue, AIUsageLog
from app.schemas.a2_contract import A2Output
from app.validators.grounding import MessageBrief
from app.validators.rulebook_checks import validate_rulebook_v0
from app.validators.sameness import validate_sameness
from app.workers.a2_personalization.brief_assembler import assemble_brief
from app.workers.a2_personalization.graph import get_rulebook_footer
from app.core.groq_provider import limiter, log_ai_usage, MODEL_LIMITS
from app.pipeline.runner import run_single_pipeline_item
from app.pipeline.providers.mock_provider import MockEmailProvider


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def test_verified_signal_flows_from_contact_into_brief(db: Session):
    """Test that contacts.verified_signal flows cleanly into MessageBrief."""
    contact = Contact(
        contact_name="Vikram Mehta",
        company_name="Heritage Organic Mart",
        email="vikram.signal@heritagemart.com",
        segment="supermarket_retail",
        country="India",
        verified_signal="Verified bio-food category buyer at Aahar Expo 2026",
    )
    db.add(contact)
    db.commit()

    segment = db.query(Segment).filter_by(segment="supermarket_retail").first()
    catalogue = db.query(Catalogue).filter_by(id=segment.catalogue_id).first()
    claims = db.query(ApprovedClaim).filter_by(segment="supermarket_retail").all()

    brief = assemble_brief(
        contact=contact,
        segment=segment,
        catalogue=catalogue,
        approved_claims=claims,
    )

    assert brief.verified_signal == "Verified bio-food category buyer at Aahar Expo 2026"

    # Cleanup
    db.delete(contact)
    db.commit()


def test_footer_excluded_from_word_count_and_sameness():
    """Test that mandatory footer is excluded from rulebook word count and sameness checking."""
    prose = (
        "Hi Vikram,\n\n"
        "We supply FSSAI and ISO certified pure organic cow ghee and cold-pressed oils. "
        "Our range is currently stocked by premium retail networks across western India.\n\n"
        "Would you be open to reviewing our product catalogue for your upcoming category review? "
        "We can courier sample jars directly to your procurement desk."
    )
    words_in_prose = len(prose.split())

    footer = (
        "Aarav Shah, B2B Procurement & Exports\n"
        "Narayani Distributors LLP\n"
        "Ahmedabad, Gujarat | contact@narayanidistributors.com\n"
        "If you'd rather not hear from us, reply STOP and we won't email you again."
    )
    words_in_footer = len(footer.split())
    full_email = f"{prose}\n\n--\n{footer}"

    # Word count bounds set to match ONLY prose
    res = validate_rulebook_v0(
        body=full_email,
        first_name="Vikram",
        word_count_min=words_in_prose - 5,
        word_count_max=words_in_prose + 5,
    )
    # If footer were counted, word count would be words_in_prose + words_in_footer, causing failure
    assert res.passed is True, f"Rulebook failed with errors: {res.errors}"

    # Sameness test: two different emails sharing identical footers must NOT trigger sameness
    prose_two = (
        "Hi Vikram,\n\n"
        "We produce small-batch artisanal forest honey and stone-ground spices for organic stores. "
        "Every batch comes with complete lab reports and NPOP organic certification.\n\n"
        "Could we send a sample tasting kit to your procurement team this Thursday? "
        "We can include our wholesale export catalogue as well."
    )
    email_two = f"{prose_two}\n\n--\n{footer}"

    sameness_res = validate_sameness(
        body=email_two,
        prospect_recent_messages=[full_email],
    )
    assert sameness_res.passed is True, f"Sameness failed with violations: {sameness_res.violations}"


def test_contract_v2_conformance_and_assembly():
    """Test A2 contract v2 separates components, verifies segment claims/ctas, and assembles prose."""
    output = A2Output(
        subject="Organic Retail Supply for Heritage Mart",
        opener="Hi Vikram, I noticed Heritage Mart expanded its organic dairy shelves.",
        body="We supply NPOP certified A2 Desi Cow Ghee with complete batch traceability. Our retail formats are shelf-ready with EAN barcoding.",
        cta="Would you like to review our retail catalogue and sample pack?",
        cta_id="cta_catalogue_sample",
        angle="angle_shelf_fit",
        claims_used=[28, 29],
        product_terms_used=["EAN", "barcode"],
        confidence=0.92,
    )

    # Valid segment claims
    errors = output.verify_segment_conformance(
        allowed_angles=["angle_shelf_fit", "angle_direct_margin"],
        allowed_ctas=["cta_catalogue_sample", "cta_quick_discussion"],
        segment_claim_ids=[28, 29, 30],
    )
    assert len(errors) == 0

    # Invalid claim ID not in segment
    invalid_errors = output.verify_segment_conformance(
        allowed_angles=["angle_shelf_fit"],
        allowed_ctas=["cta_catalogue_sample"],
        segment_claim_ids=[1, 2, 3],  # Claims 28 and 29 not present
    )
    assert len(invalid_errors) == 2
    assert "Claim ID [28]" in invalid_errors[0]

    # Full assembly with footer
    footer = "Narayani Distributors | Reply STOP to unsubscribe."
    assembled = output.assemble_full_email(footer)
    assert "Hi Vikram" in assembled
    assert "Reply STOP to unsubscribe." in assembled
    assert "--\n" in assembled


def test_rate_limiter_queries_ai_usage_log_and_stops_on_exhaustion(db: Session):
    """Test that rate limiter derives daily usage from ai_usage_log and stops sending when exhausted."""
    model_name = "llama-3.3-70b-versatile"

    # Clean existing logs for test model today
    today_start = limiter.get_today_reset_boundary()
    db.query(AIUsageLog).filter(
        AIUsageLog.model_name == model_name,
        AIUsageLog.created_at >= today_start,
    ).delete()
    db.commit()

    # Initial check: within budget
    can_send, msg = limiter.check_can_send(db, model_name)
    assert can_send is True
    assert msg == "OK"

    # Log 96,000 tokens (exceeds 95% of 100k cap)
    log_ai_usage(
        session=db,
        agent_name="A2_writer",
        model_name=model_name,
        call_type="draft",
        tokens_prompt=80000,
        tokens_completion=16000,
        status="success",
    )

    # Now check_can_send must be False with plain explanation
    can_send_after, msg_after = limiter.check_can_send(db, model_name)
    assert can_send_after is False
    assert "Paused: Groq daily token allowance nearly exhausted" in msg_after

    # Cleanup
    db.query(AIUsageLog).filter(
        AIUsageLog.model_name == model_name,
        AIUsageLog.created_at >= today_start,
    ).delete()
    db.commit()


def test_provider_error_does_not_count_as_attempt_and_retries(db: Session):
    """Test that Groq provider errors (429, timeout) do not increment attempts and release lease."""
    contact = Contact(
        contact_name="Ramesh Gupta",
        company_name="Gupta Wholesale Corp",
        email="ramesh.provider.err@example.com",
        segment="distributor_wholesale",
        country="India",
    )
    db.add(contact)
    db.flush()

    q_item = OutboundQueue(
        contact_id=contact.id,
        status="eligible",
        attempts=0,
    )
    db.add(q_item)
    db.commit()

    # Simulate provider failure returning waiting_for_ai
    mock_provider = MockEmailProvider()
    with patch("app.pipeline.runner.run_a2_for_contact") as mock_a2:
        mock_a2.return_value = {
            "status": "waiting_for_ai",
            "hold_reason": "Groq HTTP 429: Rate limit exceeded (retry in 10s)",
            "attempt": 1,
        }

        res = run_single_pipeline_item(
            session=db,
            worker_id="test_worker_provider_err",
            provider=mock_provider,
        )

        assert res["status"] == "waiting_for_ai"
        assert res["attempts"] == 0  # CRITICAL: attempts was NOT incremented

        # Check queue item in DB
        db.refresh(q_item)
        assert q_item.status == "eligible"  # Returned to pool
        assert q_item.attempts == 0  # Never incremented
        assert q_item.locked_by is None

    # Cleanup
    db.delete(q_item)
    db.delete(contact)
    db.commit()


def test_attempt_history_and_failed_validator_persisted(db: Session):
    """Test that when a draft is held, attempt_history and failed_validator are persisted in DB."""
    contact = Contact(
        contact_name="Aanchal Verma",
        company_name="Verma Retail Mart",
        email="aanchal.history@vermamart.com",
        segment="supermarket_retail",
        country="India",
    )
    db.add(contact)
    db.flush()

    q_item = OutboundQueue(
        contact_id=contact.id,
        status="eligible",
        attempts=0,
    )
    db.add(q_item)
    db.commit()

    mock_provider = MockEmailProvider()
    mock_history = [
        {"attempt": 1, "failed_validator": "rulebook", "errors": ["Exclamation mark detected"]},
        {"attempt": 2, "failed_validator": "catalogue_grounding", "errors": ["Unapproved term 'pickles'"]},
    ]

    with patch("app.pipeline.runner.run_a2_for_contact") as mock_a2:
        mock_a2.return_value = {
            "status": "held",
            "attempt": 2,
            "hold_reason": "Validator hold (2 attempts: catalogue grounding violation)",
            "failed_validator": "catalogue_grounding",
            "attempt_history": mock_history,
            "draft_body": "Hi Aanchal,\n\nWe supply organic products.\n\nWould you like a catalogue?",
            "full_email_body": "Hi Aanchal,\n\nWe supply organic products.\n\nWould you like a catalogue?\n\n--\nNarayani Distributors",
        }

        res = run_single_pipeline_item(
            session=db,
            worker_id="test_worker_history",
            provider=mock_provider,
        )

        assert res["status"] == "held"
        assert res["failed_validator"] == "catalogue_grounding"
        assert res["attempts"] == 2

        # Check database persistence
        db.refresh(q_item)
        assert q_item.status == "held"
        assert q_item.attempts == 2
        assert q_item.failed_validator == "catalogue_grounding"
        assert isinstance(q_item.attempt_history, list)
        assert len(q_item.attempt_history) == 2
        assert q_item.attempt_history[0]["failed_validator"] == "rulebook"

    # Cleanup
    db.delete(q_item)
    db.delete(contact)
    db.commit()
