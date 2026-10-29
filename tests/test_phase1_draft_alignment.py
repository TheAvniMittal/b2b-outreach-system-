import pytest
from app.schemas.a2_contract import A2Output, MessageMetadata
from app.validators.rulebook_checks import validate_rulebook_v0
from app.validators.grounding import MessageBrief, validate_grounding


def test_assemble_prose_separates_line1_greeting():
    """Verifies that assemble_prose_for_validation guarantees Line 1 is strictly 'Hi <Name>,'."""
    out = A2Output(
        subject="Wholesale Distribution Partnership",
        opener="Hi Manoj, your expansion into Gujarat wholesale branches aligns well with our current regional distribution network.",
        body="We partner with leading regional distributors to ensure consistent stock replenishment, dependable dispatch schedules, and dedicated account support.\n\nOur full product range includes authentic packaged namkeen and snack varieties manufactured under strict FSSAI compliance. Each product is packaged for high inventory turnover and backed by dedicated territory protection for distributors across Western India.",
        cta="We would be pleased to share our full pricing schedule. Would you be open to reviewing our wholesale product catalogue this week?",
        cta_id="catalogue",
        angle="margin_opportunity",
        claims_used=[1],
        product_terms_used=["namkeen", "snacks"],
        confidence=0.85,
    )

    assembled = out.assemble_prose_for_validation()
    lines = [l.strip() for l in assembled.split("\n") if l.strip()]

    # Line 1 must strictly be "Hi Manoj,"
    assert lines[0] == "Hi Manoj,"

    # Run Rulebook validator
    res = validate_rulebook_v0(
        body=assembled,
        first_name="Manoj",
        word_count_min=80,
        word_count_max=130,
    )

    assert res.per_rule_results.get("greeting_form") is True, f"Greeting failed: {res.errors}"
    assert res.per_rule_results.get("paragraph_count") is True, f"Paragraph count failed: {res.errors}"
    assert res.per_rule_results.get("sentence_structure") is True, f"Sentence structure failed: {res.errors}"
    assert res.per_rule_results.get("no_all_caps") is True, f"All caps failed: {res.errors}"
    assert res.passed is True, f"Draft failed rulebook with errors: {res.errors}"


def test_assemble_prose_handles_fmcg_all_caps():
    """Verifies that 'FMCG' is recognized as an allowed acronym and not flagged as illegal all-caps."""
    out = A2Output(
        subject="FMCG Distribution Network",
        opener="Hi Abhinav,\n\nYour focus on Gujarat FMCG distribution aligns well with our supply chain capabilities.",
        body="We partner with established distributors to maintain steady stock flow and reliable FMCG delivery cycles.\n\nOur portfolio offers authentic packaged namkeen and tea snacks produced under certified FSSAI standards. All product lines support solid margin bands and active territory exclusivity.",
        cta="We would welcome the chance to share our distributor price list. Would you be open to a brief call this week?",
        cta_id="price_list",
        angle="margin_opportunity",
        claims_used=[2],
        product_terms_used=["namkeen", "snacks"],
        confidence=0.88,
    )

    assembled = out.assemble_prose_for_validation()
    assert "FMCG" in assembled

    res = validate_rulebook_v0(
        body=assembled,
        first_name="Abhinav",
        word_count_min=70,
        word_count_max=130,
    )

    assert res.per_rule_results.get("no_all_caps") is True, f"FMCG flagged as illegal all-caps: {res.errors}"
    assert res.passed is True, f"Rulebook errors: {res.errors}"


def test_cross_catalogue_grounding_allows_generic_fmcg():
    """Verifies that common sector term 'fmcg' does not trigger cross-catalogue grounding check (h2)."""
    brief = MessageBrief(
        company_name="Vanguard Logistics",
        contact_name="Manoj Kumar",
        country="India",
        city="Ahmedabad",
        segment="distributor_wholesale",
        catalogue_terms=["namkeen", "papad", "ready-to-eat", "snacks", "wholesale", "territory"],
        other_catalogues_terms=["fmcg", "mixes", "sauces", "cold-pressed oils"],
        other_catalogues_term_map={"fmcg": "fmcg_distributor", "sauces": "fmcg_distributor"},
        approved_claims=[{"id": 1, "text": "Over 40 years of trusted distribution across Western India."}],
        allowed_ctas=["catalogue", "price_list", "call"],
        forbidden_topics=["FCL/container", "single_unit_retail_pricing"],
        product_category="packaged_foods",
    )

    body_text = (
        "Hi Manoj,\n\n"
        "Your focus on Gujarat FMCG wholesale aligns well with our regional distribution capabilities. "
        "We partner with commercial partners to ensure steady supply and dependable dispatch schedules.\n\n"
        "Our portfolio includes authentic packaged namkeen and snack varieties with strong shelf rotation. "
        "Over 40 years of trusted distribution across Western India ensures consistent supply for regional wholesale partners.\n\n"
        "We would be delighted to share our complete wholesale catalogue with your team. "
        "Would you be open to reviewing the product sheet this week?"
    )

    grounding_res = validate_grounding(
        body=body_text,
        subject="Wholesale distribution inquiry",
        claims_used=[1],
        product_terms_used=["namkeen", "snacks"],
        cta="catalogue",
        brief=brief,
    )

    # Check that h2 did not flag 'fmcg'
    h2_errors = [e for e in grounding_res.errors if "Check (h2)" in e and "fmcg" in e]
    assert len(h2_errors) == 0, f"Check h2 incorrectly flagged 'fmcg': {h2_errors}"
    assert grounding_res.per_check_results.get("check_h_catalogue_consistency") is True, f"Grounding check h failed: {grounding_res.errors}"
