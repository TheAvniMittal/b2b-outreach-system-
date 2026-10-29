"""
Tests for app/deliverability/bounce_classifier.py (VISION.md §7.5, §13.3).
"""

import pytest
from app.deliverability.bounce_classifier import (
    classify_bounce,
    BounceCategory,
    BounceClassification,
    extract_codes_from_text,
)


def test_hard_bounce_codes():
    """5.1.1, 5.1.2, 5.2.1 -> permanent suppression, no retry."""
    for dsn in ["5.1.1", "5.1.2", "5.2.1"]:
        res = classify_bounce(dsn_code=dsn)
        assert res.category == BounceCategory.HARD
        assert res.permanent_suppress is True
        assert res.should_retry is False
        assert res.action == "permanent_suppression"
        assert res.is_bounce is True


def test_soft_bounce_codes():
    """4.2.2, 4.3.x, temp errors -> retry with backoff then suppress."""
    for dsn in ["4.2.2", "4.3.0", "4.3.2", "5.2.2", "4.4.1"]:
        res = classify_bounce(dsn_code=dsn)
        assert res.category == BounceCategory.SOFT
        assert res.permanent_suppress is False
        assert res.should_retry is True
        assert res.action == "retry_with_backoff"
        assert res.is_bounce is True


def test_policy_reputation_block():
    """5.7.1, spam refusals -> do NOT suppress recipient, raise deliverability alarm."""
    for dsn in ["5.7.1", "5.7.0", "5.7.25"]:
        res = classify_bounce(dsn_code=dsn)
        assert res.category == BounceCategory.POLICY_BLOCK
        # Inviolable requirement: never suppress recipient on policy block
        assert res.permanent_suppress is False
        assert res.raise_alarm is True
        assert res.action == "deliverability_alarm"
        assert res.is_bounce is True

    # Test spam refusal diagnostic text
    text_sample = "554 5.7.1 Our system has detected that this message is likely unsolicited mail. Client blocked by Spamhaus."
    res = classify_bounce(diagnostic_text=text_sample)
    assert res.category == BounceCategory.POLICY_BLOCK
    assert res.permanent_suppress is False
    assert res.raise_alarm is True


def test_throttle_rate_limit():
    """4.7.x rate-limited, 421 -> slow send rate, requeue, not a bounce."""
    for dsn in ["4.7.0", "4.7.1", "4.7.28"]:
        res = classify_bounce(dsn_code=dsn)
        assert res.category == BounceCategory.THROTTLE
        assert res.permanent_suppress is False
        assert res.should_retry is True
        assert res.slow_send_rate is True
        assert res.is_bounce is False
        assert res.action == "slow_send_rate_and_requeue"

    # Test throttle text
    text_sample = "421 4.7.0 [TSS04] Message from IP temporarily deferred due to user rate limit. Try again later."
    res = classify_bounce(diagnostic_text=text_sample)
    assert res.category == BounceCategory.THROTTLE
    assert res.is_bounce is False
    assert res.slow_send_rate is True
    assert res.should_retry is True
    assert res.permanent_suppress is False


def test_text_extraction():
    """Extract codes and classify correctly from raw diagnostic strings."""
    raw1 = "Action: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 5.1.1 <nobody@domain.invalid>: Recipient address rejected: User unknown"
    res1 = classify_bounce(diagnostic_text=raw1)
    assert res1.category == BounceCategory.HARD
    assert res1.dsn_code == "5.1.1"
    assert res1.permanent_suppress is True

    raw2 = "452 4.2.2 Mailbox is full / quota exceeded"
    res2 = classify_bounce(diagnostic_text=raw2)
    assert res2.category == BounceCategory.SOFT
    assert res2.should_retry is True
    assert res2.permanent_suppress is False
