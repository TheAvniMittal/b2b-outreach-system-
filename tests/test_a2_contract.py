import pytest
from app.schemas.a2_contract import MessageMetadata, A2Output


def test_subject_length_validation():
    # <= 90 chars passes
    meta = MessageMetadata(
        subject="Spice Supply Partnership Inquiry - Narayani Distributors",
        angle_used="compliance_first",
        cta="spec_sheet",
        claims_used=[1],
        product_terms_used=["spices"],
        confidence=0.85,
    )
    assert len(meta.subject) <= 90

    # > 90 chars fails
    long_subject = "A" * 91
    with pytest.raises(ValueError, match="at most 90 characters"):
        MessageMetadata(
            subject=long_subject,
            angle_used="compliance_first",
            cta="spec_sheet",
            claims_used=[1],
            confidence=0.85,
        )


def test_confidence_above_point_seven_requires_claims():
    # confidence >= 0.7 without claims_used fails
    with pytest.raises(ValueError, match="requires claims_used to be non-empty"):
        MessageMetadata(
            subject="Valid Subject",
            angle_used="compliance_first",
            cta="spec_sheet",
            claims_used=[],
            confidence=0.75,
        )

    # confidence < 0.7 with empty claims_used passes validation
    meta_low = MessageMetadata(
        subject="Valid Subject",
        angle_used="compliance_first",
        cta="spec_sheet",
        claims_used=[],
        confidence=0.65,
    )
    assert meta_low.confidence == 0.65


def test_cta_or_angle_unknown_forces_hold():
    # angle_used == unknown
    meta_angle_unknown = MessageMetadata(
        subject="Valid Subject",
        angle_used="unknown",
        cta="spec_sheet",
        claims_used=[],
        confidence=0.5,
    )
    assert meta_angle_unknown.is_hold_forced() is True

    # cta == unknown
    meta_cta_unknown = MessageMetadata(
        subject="Valid Subject",
        angle_used="compliance_first",
        cta="unknown",
        claims_used=[],
        confidence=0.5,
    )
    assert meta_cta_unknown.is_hold_forced() is True

    # both valid
    meta_valid = MessageMetadata(
        subject="Valid Subject",
        angle_used="compliance_first",
        cta="spec_sheet",
        claims_used=[1],
        confidence=0.8,
    )
    assert meta_valid.is_hold_forced() is False


def test_veto_skip_reason():
    meta_veto = MessageMetadata(
        subject="Valid Subject",
        angle_used="unknown",
        cta="unknown",
        confidence=0.5,
        skip_reason="Retail consumer inquiry, not a B2B buyer.",
    )
    assert meta_veto.is_veto() is True


def test_a2_output_separation():
    body = "Hi Rohan,\n\nNarayani Distributors supplies certified spices.\n\nBest regards,\nAarav"
    meta = MessageMetadata(
        subject="Spice Supply",
        angle_used="compliance_first",
        cta="spec_sheet",
        claims_used=[1],
        confidence=0.85,
    )
    output = A2Output(body_text=body, metadata=meta)
    assert output.body_text == body
    assert output.metadata.subject == "Spice Supply"
    hash_val = output.compute_opening_hash()
    assert len(hash_val) == 8
