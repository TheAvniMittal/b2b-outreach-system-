import pytest
from app.pipeline.verify_email import verify_email_stages_0_to_2

TEST_GMAIL_ADDRESSES = [
    "avnimittal900+prospect1@gmail.com",
    "avnimittal900+prospect2@gmail.com",
    "avnimittal900+prospect3@gmail.com",
    "avnimittal900+prospect4@gmail.com",
    "avnimittal900+prospect5@gmail.com",
    "avnimittal900+prospect6@gmail.com",
    "avnimittal900+prospect7@gmail.com",
    "avnimittal900+prospect8@gmail.com",
    "avnimittal900+prospect9@gmail.com",
    "avnimittal900@gmail.com",
]

def test_all_10_gmail_addresses_pass_stages_0_to_2():
    for addr in TEST_GMAIL_ADDRESSES:
        res = verify_email_stages_0_to_2(addr)
        assert res.is_valid is True, f"Address {addr} failed verification: {res.reason}"
        assert res.status == "valid"
        assert res.stage_reached == 2
        assert res.reason is None


def test_stage_0_catches_invalid_syntax():
    invalid_syntaxes = ["notanemail", "user@", "@domain.com", "user name@domain.com", "user@domain..com"]
    for bad in invalid_syntaxes:
        res = verify_email_stages_0_to_2(bad)
        assert res.is_valid is False
        assert res.status == "invalid"
        assert res.stage_reached == 0
        assert res.reason == "invalid_syntax"


def test_stage_1_catches_nonexistent_mx():
    res = verify_email_stages_0_to_2("test@nonexistentdomain999888777666.org")
    assert res.is_valid is False
    assert res.status == "invalid"
    assert res.stage_reached == 1
    assert "no_mx_record" in res.reason or "dns_lookup_error" in res.reason


def test_stage_2_catches_disposable_domain():
    res = verify_email_stages_0_to_2("user@mailinator.com")
    assert res.is_valid is False
    assert res.status == "invalid"
    assert res.stage_reached == 2
    assert res.reason == "disposable_domain"
