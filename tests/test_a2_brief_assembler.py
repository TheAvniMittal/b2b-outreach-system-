import pytest
from app.workers.a2_personalization.brief_assembler import assemble_brief, A2_INPUT_CEILING


def test_brief_assembler_filters_out_commercial_claims():
    """Verify that claim_type == 'commercial' is strictly excluded (§10.8, §10.7)."""
    contact = {
        "company_name": "Al Faisal Import Trading",
        "contact_name": "Rohan Mehta",
        "country": "India",
        "city": "Mumbai",
    }
    segment = {
        "segment": "importer_india",
        "allowed_angles": ["compliance_first", "certification_led"],
        "allowed_ctas": ["spec_sheet", "certifications_pack"],
        "forbidden_topics": ["consumer_pack_sizes"],
        "register_spec": "Formal, document-forward",
        "target_words_min": 100,
        "target_words_max": 150,
    }
    catalogue = {
        "summary": "Full compliance catalogue with HS codes and FSSAI specs.",
        "registered_term_list": ["spices", "turmeric", "FSSAI"],
    }
    claims = [
        {"id": 1, "segment": "importer_india", "claim_type": "certification", "text": "Our facility holds FSSAI certification."},
        {"id": 2, "segment": "importer_india", "claim_type": "commercial", "text": "Standard FOB terms are available on request."},
        {"id": 3, "segment": "importer_india", "claim_type": "capability", "text": "We supply whole and ground spices in bulk."},
        {"id": 4, "segment": "importer_india", "claim_type": "commercial", "text": "Margin structures and territory terms available."},
        {"id": 5, "segment": "importer_india", "claim_type": "product_descriptive", "text": "Our turmeric is sourced from Maharashtra."},
    ]

    brief = assemble_brief(
        contact=contact,
        segment=segment,
        catalogue=catalogue,
        approved_claims=claims,
    )

    # Assert commercial claims were filtered out
    claim_types = [c["claim_type"] for c in brief.approved_claims]
    assert "commercial" not in claim_types
    assert len(brief.approved_claims) == 3
    claim_ids = [c["id"] for c in brief.approved_claims]
    assert 2 not in claim_ids
    assert 4 not in claim_ids


def test_pii_minimisation_and_fenced_notes():
    """Verify PII minimisation (§14.7) and untrusted notes fencing (§8.8)."""
    contact = {
        "company_name": "Al Faisal Import Trading",
        "contact_name": "Rohan Mehta",
        "country": "India",
        "email": "rohan@example.com",
        "notes": "Looking to expand into new markets.",
    }
    segment = {"segment": "importer_india"}
    catalogue = {"summary": "Catalogue summary", "registered_term_list": []}
    claims = []

    # Default: notes and PII omitted
    brief_default = assemble_brief(
        contact=contact,
        segment=segment,
        catalogue=catalogue,
        approved_claims=claims,
        include_notes=False,
        include_pii=False,
    )
    assert brief_default.untrusted_notes is None
    assert "prospect_email" not in (brief_default.campaign_config or {})

    # Opted-in: notes fenced with <untrusted_data>
    brief_opted_in = assemble_brief(
        contact=contact,
        segment=segment,
        catalogue=catalogue,
        approved_claims=claims,
        include_notes=True,
        include_pii=True,
    )
    assert brief_opted_in.untrusted_notes == "<untrusted_data>Looking to expand into new markets.</untrusted_data>"
    assert brief_opted_in.campaign_config.get("prospect_email") == "rohan@example.com"


def test_input_ceiling_hard_assertion():
    """Verify that exceeding A2_INPUT_CEILING raises an error (§8.6, §9.2)."""
    contact = {
        "company_name": "Al Faisal Import Trading",
        "contact_name": "Rohan Mehta",
        "country": "India",
    }
    segment = {
        "segment": "importer_india",
        "register_spec": "Word " * 5000,  # massive register spec exceeding 4100 tokens
    }
    catalogue = {"summary": "Catalogue summary", "registered_term_list": []}
    claims = []

    with pytest.raises(ValueError, match="A2_INPUT_CEILING exceeded"):
        assemble_brief(
            contact=contact,
            segment=segment,
            catalogue=catalogue,
            approved_claims=claims,
        )
