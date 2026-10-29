import pytest
from app.validators.grounding import MessageBrief, validate_grounding
from app.db.session import get_db_session


def test_golden_test_3_certification_importer_india_fssai_passes():
    """Golden test 3: A certification claim that IS in approved_claims for importer_india (e.g. FSSAI) -> MUST PASS check (c)."""
    brief = MessageBrief(
        segment="importer_india",
        company_name="Apex Global Imports",
        contact_name="Rajesh Gupta",
        country="India",
        city="Mumbai",
        approved_claims=[
            {
                "id": 1,
                "segment": "importer_india",
                "claim_type": "certification",
                "text": "Our spice processing facility holds FSSAI certification.",
            },
            {
                "id": 2,
                "segment": "importer_india",
                "claim_type": "certification",
                "text": "Our facility is ISO 22000 certified for food safety management.",
            },
        ],
        catalogue_terms=["spices", "turmeric", "FSSAI", "ISO 22000"],
        other_catalogues_terms=["namkeen", "papad", "protein snacks"],
        allowed_ctas=["spec_sheet", "certifications_pack", "call"],
        forbidden_topics=["consumer_pack_sizes", "retail_shelf_placement"],
    )

    body = """Hi Rajesh,

Founded 1998 in Gujarat, Narayani Distributors processes and supplies premium whole spices for import and distribution. Our processing facility maintains FSSAI certification and full batch traceability. We support leading institutional buyers across India.

We would be glad to share our certifications pack and documentation for your review. Would you be open to reviewing our compliance sheet this week?

Best regards,
Aarav Shah
Narayani Distributors"""

    # We also add 1998 to campaign_config so number tracing passes
    brief.campaign_config = {"founded": "1998"}

    res = validate_grounding(
        body=body,
        subject="Spice Supply and Compliance - Narayani Distributors",
        claims_used=[1],
        product_terms_used=["spices"],
        cta="certifications_pack",
        brief=brief,
    )
    assert res.per_check_results["check_c_certifications"] is True, f"Check c failed: {res.errors}"
    assert res.passed is True, f"Grounding failed: {res.errors}"


def test_golden_test_4_certification_wellness_gym_fssai_fails():
    """Golden test 4: The SAME certification token used for a segment with no matching claim row (e.g. wellness_gym) -> MUST FAIL check (c)."""
    brief = MessageBrief(
        segment="wellness_gym",
        company_name="FitLife Nutrition Centers",
        contact_name="Amit Roy",
        country="India",
        city="Bangalore",
        approved_claims=[
            {
                "id": 31,
                "segment": "wellness_gym",
                "claim_type": "product_descriptive",
                "text": "Our wellness range includes protein-rich snacks, seed mixes, and clean-label bars.",
            },
            {
                "id": 33,
                "segment": "wellness_gym",
                "claim_type": "certification",
                "text": "Our wellness range carries India Organic (NPOP) certification on eligible SKUs.",
            },
        ],
        catalogue_terms=["protein snacks", "clean-label", "seed mixes"],
        other_catalogues_terms=["spices", "turmeric", "namkeen"],
        allowed_ctas=["sample_pack", "nutrition_sheet", "call"],
        forbidden_topics=["bulk_commodity_pricing", "import_documentation_language"],
    )

    # Email body attempting to use FSSAI certification for wellness_gym
    body = """Hi Amit,

Narayani Distributors supplies clean-label health snacks and seed mixes for fitness chains. Our facility holds FSSAI certification and rigorous quality controls. We support health centers with batch consistency.

We would be glad to share our sample pack for your evaluation. Would you have ten minutes for a call this week?

Best regards,
Aarav Shah
Narayani Distributors"""

    res = validate_grounding(
        body=body,
        subject="Wellness Range Introduction - Narayani Distributors",
        claims_used=[31, 33],
        product_terms_used=["clean-label"],
        cta="sample_pack",
        brief=brief,
    )
    assert res.per_check_results["check_c_certifications"] is False
    assert res.passed is False
    assert any("FSSAI" in err and "wellness_gym" in err for err in res.errors)


def test_grounding_with_db_session():
    """Verify that check (c) queries Neon DB approved_claims accurately."""
    with get_db_session() as db:
        # importer_india has FSSAI in db
        brief_india = MessageBrief(
            segment="importer_india",
            company_name="Apex Imports",
            contact_name="Rajesh",
            country="India",
        )
        res_india = validate_grounding(
            body="Our facility holds FSSAI certification.",
            subject="Intro",
            claims_used=[],
            product_terms_used=[],
            cta="",
            brief=brief_india,
            db_session=db,
        )
        assert res_india.per_check_results["check_c_certifications"] is True

        # wellness_gym does NOT have FSSAI in db
        brief_gym = MessageBrief(
            segment="wellness_gym",
            company_name="FitLife",
            contact_name="Amit",
            country="India",
        )
        res_gym = validate_grounding(
            body="Our facility holds FSSAI certification.",
            subject="Intro",
            claims_used=[],
            product_terms_used=[],
            cta="",
            brief=brief_gym,
            db_session=db,
        )
        assert res_gym.per_check_results["check_c_certifications"] is False


def test_check_e_excludes_untrusted_notes():
    """Imported free-text notes are explicitly excluded from entity allowlist (§8.8, §10.1e)."""
    brief = MessageBrief(
        segment="importer_india",
        company_name="Apex Global Imports",
        contact_name="Rajesh Gupta",
        country="India",
        city="Mumbai",
        untrusted_notes="Prospect requested expansion into Berlin through partner BavarianTrade Corp.",
        approved_claims=[
            {
                "id": 1,
                "segment": "importer_india",
                "text": "Our spice facility in Gujarat exports to domestic and international clients.",
            }
        ],
    )

    # Injected entity BavarianTrade from untrusted notes used in body
    body = "Hi Rajesh,\n\nWe would love to collaborate with BavarianTrade Corp for your distribution.\n\nBest regards,\nAarav"
    res = validate_grounding(
        body=body,
        subject="Intro",
        claims_used=[1],
        product_terms_used=[],
        cta="",
        brief=brief,
    )
    assert res.per_check_results["check_e_entity_allowlist"] is False
    assert any("BavarianTrade" in err for err in res.errors)


def test_check_f_past_relationship_verbs_rejected():
    """Past relationship verbs denylist rejects cold outreach hallucinating prior contact."""
    brief = MessageBrief(
        segment="importer_india",
        company_name="Apex Imports",
        contact_name="Rajesh",
        country="India",
    )
    fail_verbs = [
        "As discussed, here is our spice catalogue.",
        "Regarding your order, we have updated specifications.",
        "Following our call earlier today, sharing details.",
        "Last time we reviewed your requirements.",
        "We previously reached out about agro commodities.",
    ]
    for text in fail_verbs:
        res = validate_grounding(
            body=text,
            subject="Update",
            claims_used=[],
            product_terms_used=[],
            cta="",
            brief=brief,
        )
        assert res.per_check_results["check_f_past_relationship"] is False


def test_check_b_number_tracing():
    """Ungrounded numbers in body fail check (b)."""
    brief = MessageBrief(
        segment="importer_india",
        company_name="Apex Imports",
        contact_name="Rajesh",
        country="India",
        approved_claims=[
            {"id": 1, "text": "Founded 40 years ago in Gujarat."}
        ],
    )
    # Number 40 is grounded, but 99 is ungrounded
    body = "Founded 40 years ago, we operate 99 distribution hubs across the country."
    res = validate_grounding(
        body=body,
        subject="Intro",
        claims_used=[1],
        product_terms_used=[],
        cta="",
        brief=brief,
    )
    assert res.per_check_results["check_b_number_tracing"] is False
    assert any("99" in err for err in res.errors)


def test_check_g_forbidden_topics():
    """Forbidden topics for the segment fail check (g)."""
    brief = MessageBrief(
        segment="importer_india",
        company_name="Apex Imports",
        contact_name="Rajesh",
        country="India",
        allowed_ctas=["call", "spec_sheet"],
        forbidden_topics=["retail_shelf_placement", "gym_nutrition_language", "consumer_pack_sizes"],
    )
    body = "We specialize in consumer pack sizes for modern trade counters."
    res = validate_grounding(
        body=body,
        subject="Intro",
        claims_used=[],
        product_terms_used=[],
        cta="spec_sheet",
        brief=brief,
    )
    assert res.per_check_results["check_g_playbook"] is False
    assert any("consumer_pack_sizes" in err for err in res.errors)


def test_check_h_catalogue_consistency():
    """Check h1 positive catalogue terms and h2 negative cross-catalogue terms."""
    brief = MessageBrief(
        segment="horeca_bakery",
        company_name="Royal Bakes",
        contact_name="Sunil",
        country="India",
        catalogue_terms=["flours", "sugars", "fillings", "bakery"],
        other_catalogues_terms=["namkeen", "dates", "almonds", "protein snacks"],
    )
    # Mentions dates and almonds which belong to importer_uae catalogue
    body = "We supply premium flours, sugars, and imported dates for bakery units."
    res = validate_grounding(
        body=body,
        subject="Bakery Ingredients",
        claims_used=[],
        product_terms_used=["flours", "sugars"],
        cta="",
        brief=brief,
    )
    assert res.per_check_results["check_h_catalogue_consistency"] is False
    assert any("dates" in err for err in res.errors)
