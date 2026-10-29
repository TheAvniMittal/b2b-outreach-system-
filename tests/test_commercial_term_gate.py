import pytest
from app.validators.commercial_term_gate import validate_commercial_terms


def test_golden_test_1_founded_40_years_ago_must_pass():
    """Golden test 1: 'Founded 40 years ago' -> MUST PASS the commercial-term gate."""
    body = "Hi Rajesh,\n\nFounded 40 years ago, Narayani Distributors supplies premium spices.\nWe can send our catalogue for your review.\n\nBest regards,\nNarayani Team"
    res = validate_commercial_terms(body=body, subject="Spice Supply Introduction")
    assert res.passed is True, f"Expected pass, got violations: {res.violations}"


def test_golden_test_2_moq_is_40_mt_must_fail():
    """Golden test 2: 'MOQ is 40 MT' -> MUST FAIL the commercial-term gate."""
    body = "Hi Rajesh,\n\nOur MOQ is 40 MT for whole turmeric.\nWe can send our catalogue for your review.\n\nBest regards,\nNarayani Team"
    res = validate_commercial_terms(body=body, subject="Spice Supply Introduction")
    assert res.passed is False
    assert len(res.violations) > 0
    assert any("MOQ" in v or "MT" in v for v in res.violations)


def test_bare_numerics_pass():
    """Bare numerics like 12 categories, three generations, 1998 must pass."""
    pass_cases = [
        "Founded 40 years ago in Gujarat.",
        "Our export range spans 12 categories of agricultural produce.",
        "A family-run enterprise built over three generations of trade.",
        "Established in 1998 with domestic packaging units.",
    ]
    for text in pass_cases:
        res = validate_commercial_terms(body=text)
        assert res.passed is True, f"Failed for: {text}, violations: {res.violations}"


def test_layer_1_numeric_proximity_failures():
    fail_cases = [
        ("Pricing is $4.20 per unit.", "$"),
        ("We sell at ₹380 per unit.", "₹"),
        ("Current rate is AED 15.", "AED"),
        ("The consignment is 500 kg.", "kg"),
        ("We can supply 2 containers monthly.", "container"),
        ("Payment is 30% advance on order confirmation.", "advance"),
        ("Standard payment is net 60.", "net"),
        ("We accept LC 90 days.", "LC"),
        ("Ships in 15 days from JNPT port.", "ships in"),
        ("Guaranteed 12 months shelf life.", "shelf"),
        ("Offering 8% margin for category distributors.", "margin"),
        ("Offering 5% discount on bulk lots.", "discount"),
    ]
    for text, keyword in fail_cases:
        res = validate_commercial_terms(body=text)
        assert res.passed is False, f"Expected fail for '{text}'"
        assert any(keyword.lower() in v.lower() for v in res.violations)


def test_layer_2_spelled_number_words_and_indian_currency():
    fail_cases = [
        "We handle forty metric tonnes on each shipment.",
        "Our facility produces two lakh units every quarter.",
        "Between twenty and forty MT can be shipped.",
        "Special price Rs.380/- per pack.",
        "Priced at INR 380 per box.",
        "Available at 380/- only.",
    ]
    for text in fail_cases:
        res = validate_commercial_terms(body=text)
        assert res.passed is False, f"Expected fail for '{text}'"


def test_layer_3_unit_free_commitments():
    fail_cases = [
        "We offer competitive pricing across all categories.",
        "Get in touch for the best price.",
        "Our team provides special rates for volume partners.",
        "We are flexible on minimums for new accounts.",
        "Our minimums are flexible depending on route.",
        "Substantial discount available for early signups.",
        "We offer attractive terms for state partners.",
        "Prices are negotiable upon inquiry.",
        "Rates as low as standard mandi rates.",
        "We can grant exclusive distributorship.",
        "We offer sole rights in your region.",
    ]
    for text in fail_cases:
        res = validate_commercial_terms(body=text)
        assert res.passed is False, f"Expected fail for '{text}'"


def test_conditional_offer_distinction():
    # Offers with commercial nouns MUST FAIL
    commercial_offers = [
        "We can offer competitive rates for your network.",
        "We are able to offer pricing details directly.",
        "Happy to offer special terms for your inquiry.",
        "We can offer discount structures.",
        "We can offer flexible credit.",
    ]
    for text in commercial_offers:
        res = validate_commercial_terms(body=text)
        assert res.passed is False, f"Expected fail for commercial offer: '{text}'"

    # Offers with informational / catalogue / sample nouns MUST PASS
    non_commercial_offers = [
        "We can offer our catalogue for your review.",
        "We are able to offer samples of our organic spices.",
        "Happy to offer a spec sheet for your quality team.",
        "We can offer a brief introductory call.",
        "Happy to offer client references in your market.",
    ]
    for text in non_commercial_offers:
        res = validate_commercial_terms(body=text)
        assert res.passed is True, f"Expected pass for non-commercial offer: '{text}', violations: {res.violations}"


def test_subject_commercial_terms():
    # Subject containing commercial term fails
    res = validate_commercial_terms(
        body="Hi Rajesh,\n\nWe provide agricultural products.\n\nBest regards,\nTeam",
        subject="Best price for turmeric - Narayani"
    )
    assert res.passed is False
    assert any("[Subject]" in v for v in res.violations)
