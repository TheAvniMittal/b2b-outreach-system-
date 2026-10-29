"""
Phase 3 Tone Studio to A2 Integration Tests (§A, §B).
Verifies:
1. When a segment has an approved tone reference, A2 injects only its tone_notes (not full draft text).
2. When no approved tone reference exists, A2 renders "Tone reference: none".
3. Sameness gate flags and blocks near-copies of the approved Tone Studio sample (threshold >= 0.50).
4. Sameness gate passes novel drafts that adhere to the segment voice without copying text.
5. End-to-end integration: approving a tone reference immediately feeds A2's prompt for that segment.
"""

import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.db.models import Contact, Segment, Catalogue, ApprovedClaim, ToneReference
from app.validators.sameness import validate_sameness, TONE_REFERENCE_THRESHOLD
from app.workers.a2_personalization.graph import draft_node, sameness_check_node, A2State
from app.workers.a2_personalization.service import run_a2_for_contact
from app.workers.a2_personalization.brief_assembler import assemble_brief
from app.workers.tone_studio.service import approve_tone_reference


@pytest.fixture
def db():
    with get_db_session() as session:
        yield session


def test_tone_renders_none_when_no_active_tone():
    """Verify that when no tone reference is present in state, prompt renders 'Tone reference: none'."""
    mock_brief = MagicMock()
    mock_brief.segment = "distributor_wholesale"
    mock_brief.catalogue_terms = ["wholesale", "namkeen"]
    mock_brief.product_category = "packaged foods"
    mock_brief.campaign_config = {"target_words_min": 90, "target_words_max": 130}
    mock_brief.company_name = "Test Enterprise"
    mock_brief.contact_name = "Priya Sharma"
    mock_brief.country = "India"
    mock_brief.city = "Mumbai"
    mock_brief.allowed_ctas = ["catalogue"]
    mock_brief.forbidden_topics = []
    mock_brief.approved_claims = []
    mock_brief.untrusted_notes = None

    state: A2State = {
        "brief": mock_brief,
        "first_name": "Priya",
        "attempt": 1,
        "draft_body": "",
        "full_email_body": "",
        "output": None,
        "metadata": None,
        "rulebook_errors": [],
        "grounding_errors": [],
        "sameness_errors": [],
        "segment_conformance_errors": [],
        "previous_errors": [],
        "status": "pending",
        "hold_reason": None,
        "failed_validator": None,
        "veto_reason": None,
        "attempt_history": [],
        "prospect_recent_messages": [],
        "segment_recent_messages": [],
        "recent_opening_sentences": [],
        "opening_hashes": [],
        "phrases_to_avoid": [],
        "tone_notes": None,  # No tone reference
        "tone_sample_reference": None,
    }

    with patch("app.workers.a2_personalization.graph.call_groq_a2") as mock_groq:
        mock_output = MagicMock()
        mock_output.assemble_prose_for_validation.return_value = "Sample draft prose"
        mock_output.assemble_full_email.return_value = "Sample draft prose\n--\nFooter"
        mock_output.is_veto.return_value = False
        mock_output.is_hold_forced.return_value = False
        mock_output.verify_segment_conformance.return_value = []
        mock_groq.return_value = (mock_output, 100, 50)

        draft_node(state)

        # Inspect system prompt passed to call_groq_a2
        system_prompt_arg = mock_groq.call_args[0][0]
        assert "Tone reference: none" in system_prompt_arg


def test_tone_notes_injected_into_a2_prompt_when_present():
    """Verify that when tone_notes is present, only tone notes are injected, not full draft text."""
    mock_brief = MagicMock()
    mock_brief.segment = "horeca_bakery"
    mock_brief.catalogue_terms = ["flours", "bakery"]
    mock_brief.product_category = "commercial ingredients"
    mock_brief.campaign_config = {"target_words_min": 80, "target_words_max": 120}
    mock_brief.company_name = "Artisan Pastry Works"
    mock_brief.contact_name = "Chef Vikram"
    mock_brief.country = "India"
    mock_brief.city = "Bangalore"
    mock_brief.allowed_ctas = ["sample_pack"]
    mock_brief.forbidden_topics = []
    mock_brief.approved_claims = []
    mock_brief.untrusted_notes = None

    custom_tone_notes = "Warm, consultative, artisan-respectful tone focusing on kitchen consistency."
    full_sample_draft = "This is a full draft text from Tone Studio that should NOT be in the system prompt."

    state: A2State = {
        "brief": mock_brief,
        "first_name": "Vikram",
        "attempt": 1,
        "draft_body": "",
        "full_email_body": "",
        "output": None,
        "metadata": None,
        "rulebook_errors": [],
        "grounding_errors": [],
        "sameness_errors": [],
        "segment_conformance_errors": [],
        "previous_errors": [],
        "status": "pending",
        "hold_reason": None,
        "failed_validator": None,
        "veto_reason": None,
        "attempt_history": [],
        "prospect_recent_messages": [],
        "segment_recent_messages": [],
        "recent_opening_sentences": [],
        "opening_hashes": [],
        "phrases_to_avoid": [],
        "tone_notes": custom_tone_notes,  # Injected
        "tone_sample_reference": full_sample_draft,
    }

    with patch("app.workers.a2_personalization.graph.call_groq_a2") as mock_groq:
        mock_output = MagicMock()
        mock_output.assemble_prose_for_validation.return_value = "Sample prose"
        mock_output.assemble_full_email.return_value = "Sample prose\n--\nFooter"
        mock_output.is_veto.return_value = False
        mock_output.is_hold_forced.return_value = False
        mock_output.verify_segment_conformance.return_value = []
        mock_groq.return_value = (mock_output, 100, 50)

        draft_node(state)

        system_prompt_arg = mock_groq.call_args[0][0]
        # Tone notes must be included
        assert custom_tone_notes in system_prompt_arg
        # Full draft body must NOT be included in system prompt (tone guidance only)
        assert full_sample_draft not in system_prompt_arg


def test_sameness_blocks_near_copy_of_tone_sample():
    """Verify that sameness check fails when draft is a near-copy of Tone Studio approved sample."""
    sample_draft = (
        "Hi Priya,\n\n"
        "We noticed Apex Wholesale's active expansion across regional FMCG supply chains in western India. "
        "Narayani Distributors provides certified, high-turnover grocery lines and packaged staples specifically "
        "tailored for wholesale distribution networks. Our logistics ensure batch consistency, direct territory exclusivity "
        "options, and reliable fulfillment schedules. We partner closely with established regional distributors to maximize stock "
        "turnover and margin predictability while reducing operational overhead.\n\n"
        "Would you be open to reviewing our wholesale product catalogue and trade terms this week?\n\n"
        "Best regards,"
    )

    # Near-copy draft: changing only greeting name and one minor word
    near_copy_draft = (
        "Hi Rajesh,\n\n"
        "We noticed Apex Wholesale's active expansion across regional FMCG supply chains in western India. "
        "Narayani Distributors provides certified, high-turnover grocery lines and packaged staples specifically "
        "tailored for wholesale distribution networks. Our logistics ensure batch consistency, direct territory exclusivity "
        "options, and reliable fulfillment schedules. We partner closely with established regional distributors to maximize stock "
        "turnover and margin predictability while reducing operational overhead.\n\n"
        "Would you be open to reviewing our wholesale product catalogue and trade terms this week?\n\n"
        "Best regards,"
    )

    res = validate_sameness(
        body=near_copy_draft,
        tone_sample_reference=sample_draft,
    )

    assert res.passed is False
    assert res.tone_reference_similarity >= TONE_REFERENCE_THRESHOLD
    assert any("Tone reference copy violation" in v for v in res.violations)


def test_sameness_passes_novel_draft_in_same_tone():
    """Verify that sameness check passes when draft is genuinely novel, despite having sample reference."""
    sample_draft = (
        "Hi Priya,\n\n"
        "We noticed Apex Wholesale's active expansion across regional FMCG supply chains in western India. "
        "Narayani Distributors provides certified, high-turnover grocery lines and packaged staples specifically "
        "tailored for wholesale distribution networks. Our logistics ensure batch consistency, direct territory exclusivity "
        "options, and reliable fulfillment schedules.\n\n"
        "Would you be open to reviewing our wholesale product catalogue this week?\n\n"
        "Best regards,"
    )

    # Novel draft for another distributor with different words and angle
    novel_draft = (
        "Hi Deepak,\n\n"
        "I came across Bharat Trading's recent retail footprint expansion across Gujarat state markets. "
        "Narayani supplies accredited packaged culinary essentials engineered for high inventory velocity and steady stock turns. "
        "Our manufacturing hubs maintain disciplined dispatch lead-times and dedicated account support for bulk partners.\n\n"
        "Could we send our listing sheet for your procurement leads to evaluate this Tuesday?\n\n"
        "Kind regards,"
    )

    res = validate_sameness(
        body=novel_draft,
        tone_sample_reference=sample_draft,
    )

    assert res.passed is True
    assert res.tone_reference_similarity < TONE_REFERENCE_THRESHOLD


def test_end_to_end_approval_to_a2_run(db: Session):
    """
    Full end-to-end integration test:
    1. Create/Approve a ToneReference via approve_tone_reference().
    2. Create a test contact in that segment.
    3. Call run_a2_for_contact().
    4. Verify that A2 picks up the approved tone_notes and passes it to Groq.
    5. Clean up.
    """
    test_seg = "distributor_wholesale"
    operator = "Tone Approver"

    approved_draft = (
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
    tone_notes = "Warm partnership voice emphasizing steady wholesale turnover and territory exclusivity."

    # 1. Approve Tone Reference
    tone_ref = approve_tone_reference(
        session=db,
        segment=test_seg,
        draft_body=approved_draft,
        tone_notes=tone_notes,
        operator_name=operator,
    )

    # 2. Create test contact
    test_contact = Contact(
        company_name="Vanguard Wholesale Network",
        contact_name="Manoj Agarwal",
        designation="Procurement Director",
        segment=test_seg,
        country="India",
        email="manoj.test@vanguardwholesale.in",
        verified_signal="Expanding FMCG wholesale branches in Gujarat",
    )
    db.add(test_contact)
    db.commit()
    db.refresh(test_contact)

    # 3. Run A2 for contact with mocked Groq call
    mock_llm_json = {
        "subject": "Wholesale distribution partnership for Vanguard Wholesale",
        "opener": "Hi Manoj,\nI noticed Vanguard Wholesale's recent branch expansion across regional Gujarat markets. We have followed your wholesale distribution network with great interest.",
        "body": (
            "Narayani Distributors delivers certified packaged staples and namkeen lines tailored for wholesale networks. "
            "Our logistics ensure batch consistency, direct territory exclusivity options, and predictable delivery cycles to support high inventory turns. "
            "We partner closely with regional distribution hubs to maximize stock turnover and margin predictability while reducing operational overhead."
        ),
        "cta": "Would you be open to reviewing our wholesale catalogue and trade terms this week? We would be delighted to coordinate a brief introductory call at your convenience.",
        "cta_id": "catalogue",
        "angle": "margin_opportunity",
        "claims_used": [14],
        "product_terms_used": ["namkeen", "wholesale"],
        "confidence": 0.95,
        "skip_reason": None,
    }

    import json
    mock_response = MagicMock()
    mock_response.content = json.dumps(mock_llm_json)
    mock_response.response_metadata = {
        "token_usage": {"prompt_tokens": 500, "completion_tokens": 150}
    }

    with patch("app.workers.a2_personalization.graph.get_groq_chat") as mock_get_chat:
        mock_chat = MagicMock()
        mock_chat.invoke.return_value = mock_response
        mock_get_chat.return_value = mock_chat

        final_state = run_a2_for_contact(
            contact_id=test_contact.id,
            db=db,
        )

        assert final_state["status"] == "emitted"
        assert final_state["tone_notes"] == tone_notes
        assert final_state["tone_sample_reference"] == approved_draft

        # Check prompt sent to Groq
        chat_invoke_messages = mock_chat.invoke.call_args[0][0]
        system_msg_content = chat_invoke_messages[0].content
        assert tone_notes in system_msg_content
        assert approved_draft not in system_msg_content

    # Cleanup
    db.delete(test_contact)
    db.delete(tone_ref)
    db.commit()
