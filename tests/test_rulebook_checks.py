import pytest
from app.validators.rulebook_checks import validate_rulebook_v0


VALID_BODY = """Hi Rajesh,

Founded 40 years ago in Gujarat, Narayani Distributors supplies premium bulk spices and agro commodities to leading food businesses across India. Our packing facilities operate under stringent quality management protocols with full batch traceability. We maintain dedicated supply lines for our verified import and distribution partners nationwide.

We would be pleased to share our complete catalogue and detailed product specifications for your review. Would you be open to reviewing our documentation this week?

Best regards,
Aarav Shah
Narayani Distributors"""


def test_rulebook_valid_email_passes():
    res = validate_rulebook_v0(VALID_BODY, first_name="Rajesh")
    assert res.passed is True, f"Errors: {res.errors}"


def test_rulebook_greeting_fails_if_not_line_1_hi_first_name():
    # Dear Rajesh instead of Hi Rajesh
    body_wrong_greeting = VALID_BODY.replace("Hi Rajesh,", "Dear Rajesh,")
    res = validate_rulebook_v0(body_wrong_greeting, first_name="Rajesh")
    assert res.passed is False
    assert any("greeting" in err.lower() for err in res.errors)

    # Name mismatch
    res_mismatch = validate_rulebook_v0(VALID_BODY, first_name="Vikram")
    assert res_mismatch.passed is False
    assert any("greeting" in err.lower() for err in res_mismatch.errors)


def test_rulebook_banned_openers():
    banned_openers = [
        "I hope this email finds you well",
        "I hope you are doing well",
        "I trust this email finds you in good health",
    ]
    for opener in banned_openers:
        body = f"Hi Rajesh,\n\n{opener}. We supply agricultural produce.\nOur packaging is verified.\n\nBest regards,\nAarav"
        res = validate_rulebook_v0(body, first_name="Rajesh")
        assert res.passed is False
        assert any("banned opener" in err.lower() for err in res.errors)


def test_rulebook_banned_phrases():
    banned_phrases = ["reaching out", "leverage", "synergy", "circle back"]
    for phrase in banned_phrases:
        body = f"Hi Rajesh,\n\nWe are {phrase} regarding spice supply. We provide consistent batches across India.\n\nBest regards,\nAarav"
        res = validate_rulebook_v0(body, first_name="Rajesh")
        assert res.passed is False
        assert any("banned phrase" in err.lower() for err in res.errors)


def test_rulebook_word_count_limits():
    # Too short (<80 words)
    short_body = "Hi Rajesh,\n\nWe supply spices across India. Let us know if you need our catalogue.\n\nBest regards,\nAarav"
    res_short = validate_rulebook_v0(short_body, first_name="Rajesh", word_count_min=80, word_count_max=120)
    assert res_short.passed is False
    assert any("word count" in err.lower() for err in res_short.errors)

    # Too long (>120 words)
    long_p = " ".join(["We supply premium certified agricultural commodities across India and abroad."] * 15)
    long_body = f"Hi Rajesh,\n\n{long_p}\n\nBest regards,\nAarav"
    res_long = validate_rulebook_v0(long_body, first_name="Rajesh", word_count_min=80, word_count_max=120)
    assert res_long.passed is False
    assert any("word count" in err.lower() for err in res_long.errors)


def test_rulebook_no_exclamation_marks():
    body_exclamation = VALID_BODY.replace("this week?", "this week!")
    res = validate_rulebook_v0(body_exclamation, first_name="Rajesh")
    assert res.passed is False
    assert any("exclamation" in err.lower() for err in res.errors)


def test_rulebook_no_all_caps_words():
    # Include an all-caps word like "URGENT" or "DISCOUNT"
    body_caps = VALID_BODY.replace("premium bulk spices", "PREMIUM bulk spices")
    res = validate_rulebook_v0(body_caps, first_name="Rajesh")
    assert res.passed is False
    assert any("all-caps" in err.lower() for err in res.errors)


def test_rulebook_company_name_all_caps_allowed():
    # Company names like UNIBIC, MIPL, GFC should not trigger all-caps errors when whitelisted
    body_company = VALID_BODY.replace("across India.", "across India for UNIBIC and MIPL.")
    res = validate_rulebook_v0(body_company, first_name="Rajesh", company_name="UNIBIC Foods India Pvt Ltd")
    # UNIBIC is allowed from company_name, but MIPL is not part of UNIBIC company_name
    assert any("MIPL" in err for err in res.errors)
    assert not any("UNIBIC" in err for err in res.errors)

    # When company_name is MIPL, MIPL is allowed
    body_mipl = VALID_BODY.replace("across India.", "across India for MIPL.")
    res_mipl = validate_rulebook_v0(body_mipl, first_name="Rajesh", company_name="MIPL")
    assert res_mipl.passed is True
