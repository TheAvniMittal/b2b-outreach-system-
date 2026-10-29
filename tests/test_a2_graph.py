import pytest
from app.workers.a2_personalization.graph import (
    validator_router,
    draft_exit_router,
    parse_a2_response,
    A2State,
)
from app.schemas.a2_contract import MessageMetadata


def test_validator_router_emit_when_clean():
    state: A2State = {
        "rulebook_errors": [],
        "grounding_errors": [],
        "sameness_errors": [],
        "previous_errors": [],
        "attempt": 1,
    }
    assert validator_router(state) == "emit"


def test_validator_router_no_progress_detector():
    """If attempt N errors equal attempt N-1 errors, break immediately (NASSCOM §3.6)."""
    state: A2State = {
        "rulebook_errors": ["Word count 60 outside allowed range."],
        "grounding_errors": [],
        "sameness_errors": [],
        "previous_errors": ["Word count 60 outside allowed range."],
        "attempt": 2,
    }
    decision = validator_router(state)
    assert decision == "hold"
    assert state.get("hold_reason") == "no_progress_detector"


def test_validator_router_exhausted_attempts():
    """If attempt >= 3, break to hold."""
    state: A2State = {
        "rulebook_errors": ["Some new error on attempt 3"],
        "grounding_errors": [],
        "sameness_errors": [],
        "previous_errors": ["Previous different error on attempt 2"],
        "attempt": 3,
    }
    decision = validator_router(state)
    assert decision == "hold"
    assert state.get("hold_reason") == "exhausted_repairs"


def test_validator_router_triggers_repair():
    """On attempt 1 or 2 with different errors, routes to repair."""
    state: A2State = {
        "rulebook_errors": ["Line 1 greeting invalid."],
        "grounding_errors": [],
        "sameness_errors": [],
        "previous_errors": [],
        "attempt": 1,
    }
    assert validator_router(state) == "repair"


def test_draft_exit_router():
    # Veto status exits immediately
    veto_state = {"status": "vetoed"}
    assert draft_exit_router(veto_state) == "end"

    # Held status exits immediately
    held_state = {"status": "held"}
    assert draft_exit_router(held_state) == "end"

    # Normal draft proceeds to check
    normal_state = {"status": "pending"}
    assert draft_exit_router(normal_state) == "check"


def test_parse_a2_response_delimited():
    raw = """---BODY_TEXT---
Hi Rohan,

Founded 40 years ago in Gujarat, Narayani Distributors supplies premium bulk spices. We maintain dedicated export packing lines with full batch traceability.

We would be pleased to share our complete catalogue and product specifications. Would you be open to reviewing our documentation this week?

Best regards,
Aarav Shah
---METADATA---
{
  "subject": "Spice Supply and Documentation - Narayani Distributors",
  "angle_used": "compliance_first",
  "cta": "spec_sheet",
  "claims_used": [1],
  "product_terms_used": ["spices"],
  "confidence": 0.85,
  "skip_reason": null
}"""

    body, meta = parse_a2_response(raw)
    assert "Hi Rohan," in body
    assert meta.subject == "Spice Supply and Documentation - Narayani Distributors"
    assert meta.angle_used == "compliance_first"
    assert meta.cta == "spec_sheet"
    assert meta.claims_used == [1]
    assert meta.confidence == 0.85
    assert meta.skip_reason is None
