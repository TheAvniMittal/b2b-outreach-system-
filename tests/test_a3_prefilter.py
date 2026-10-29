import pytest
from app.workers.a3_reply_classifier.prefilter import run_prefilter
from app.schemas.a3_contract import A3Kind, A3Intent


def test_prefilter_auto_submitted_header():
    res = run_prefilter(headers={"Auto-Submitted": "auto-generated"}, subject="Inquiry")
    assert res is not None
    assert res.kind == A3Kind.AUTO_REPLY
    assert res.confidence == 1.0


def test_prefilter_x_autoreply_header():
    res = run_prefilter(headers={"X-Autoreply": "yes"}, subject="Re: products")
    assert res is not None
    assert res.kind == A3Kind.AUTO_REPLY
    assert res.confidence == 1.0


def test_prefilter_precedence_bulk():
    res = run_prefilter(headers={"Precedence": "bulk"}, subject="Update")
    assert res is not None
    assert res.kind == A3Kind.AUTO_REPLY
    assert res.confidence == 1.0


def test_prefilter_dsn_content_type():
    res = run_prefilter(
        headers={"Content-Type": "multipart/report; report-type=delivery-status; boundary=xyz"},
        subject="Delivery report"
    )
    assert res is not None
    assert res.kind == A3Kind.BOUNCE_NOTICE
    assert res.confidence == 1.0


def test_prefilter_out_of_office_subject_patterns():
    ooo_subjects = [
        "Out of Office: Annual leave",
        "Automatic reply: Re: Narayani Spices Catalogue",
        "OOO: Returning next Monday",
        "Auto-Reply: Away from the office",
        "I am on vacation until Sept 30",
        "Automated response: thanks for contacting us",
    ]
    for subj in ooo_subjects:
        res = run_prefilter(headers={}, subject=subj)
        assert res is not None, f"Failed to match OOO subject: {subj}"
        assert res.kind == A3Kind.AUTO_REPLY
        assert res.confidence == 1.0


def test_prefilter_bounce_subject_patterns():
    bounce_subjects = [
        "Undeliverable: Partnership proposal",
        "Delivery Status Notification (Failure)",
        "Mail delivery failed: returning message to sender",
        "Failure Notice: mail delivery subsystem",
    ]
    for subj in bounce_subjects:
        res = run_prefilter(headers={}, subject=subj)
        assert res is not None, f"Failed to match bounce subject: {subj}"
        assert res.kind == A3Kind.BOUNCE_NOTICE
        assert res.confidence == 1.0


def test_prefilter_passes_through_normal_reply():
    res = run_prefilter(
        headers={"Message-ID": "<12345@domain.com>"},
        subject="Re: Narayani Catalogue and Price List"
    )
    assert res is None
