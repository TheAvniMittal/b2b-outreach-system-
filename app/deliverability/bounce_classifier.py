"""
Bounce and DSN Classifier (VISION.md §7.5, §13.3).

Parses bounce/DSN codes into:
1. HARD BOUNCE: (5.1.1, 5.1.2, 5.2.1 -> permanent suppression, no retry)
2. SOFT BOUNCE: (4.2.2, 4.3.x, transient network -> retry with backoff then suppress)
3. POLICY / REPUTATION BLOCK: (5.7.1, spam refusals -> do NOT suppress recipient, raise deliverability alarm)
4. THROTTLE: (4.7.x rate-limited, 421 -> slow send rate, requeue, not a bounce)
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class BounceCategory(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    POLICY_BLOCK = "policy_block"
    THROTTLE = "throttle"
    UNKNOWN = "unknown"


@dataclass
class BounceClassification:
    category: BounceCategory
    dsn_code: Optional[str]
    smtp_code: Optional[int]
    description: str
    action: str
    permanent_suppress: bool
    should_retry: bool
    raise_alarm: bool
    slow_send_rate: bool
    is_bounce: bool = True


# Regex patterns for RFC 3463/1893 enhanced status codes (e.g. 5.1.1, 4.2.2)
DSN_REGEX = re.compile(r"\b([245]\.\d{1,3}\.\d{1,3})\b")

# Regex patterns for traditional 3-digit SMTP reply codes (e.g. 550, 421)
SMTP_REGEX = re.compile(r"\b([45]\d{2})\b")

# Keyword patterns for heuristic classification
THROTTLE_KEYWORDS = [
    "rate limit", "rate limited", "rate-limited", "too many connections",
    "too many messages", "concurrent connections", "slow down", "try again later",
    "greylisted", "greylisting", "temporarily deferred", "receiving too fast",
    "connection limit exceeded", "exceeded rate limit", "throttle", "throttled"
]

POLICY_KEYWORDS = [
    "spam", "spamhaus", "barracuda", "uribl", "blacklisted", "blocklist",
    "reputation", "poor reputation", "policy reasons", "policy violation",
    "dmarc", "spf fail", "dkim fail", "unsolicited", "bulk mail",
    "rejected due to content", "content rejected", "banned", "5.7.1"
]

HARD_BOUNCE_KEYWORDS = [
    "user unknown", "no such user", "mailbox not found", "recipient address rejected",
    "user does not exist", "address rejected", "account has been disabled",
    "inactive user", "mailbox unavailable", "does not exist", "bad destination",
    "invalid recipient", "unknown recipient", "recipient not found"
]

SOFT_BOUNCE_KEYWORDS = [
    "mailbox full", "quota exceeded", "storage allocation exceeded",
    "over quota", "disk full", "space exhausted", "system full",
    "temporary failure", "mail system full", "service temporarily unavailable"
]


def extract_codes_from_text(text: str) -> Tuple[Optional[str], Optional[int]]:
    """Extracts first DSN code and SMTP reply code from raw bounce text."""
    if not text:
        return None, None

    dsn_match = DSN_REGEX.search(text)
    dsn_code = dsn_match.group(1) if dsn_match else None

    smtp_code = None
    # Look for SMTP codes like 550, 421, 554, 452
    for m in SMTP_REGEX.finditer(text):
        candidate = int(m.group(1))
        # Ensure it's not a portion of the DSN code (e.g. 5.1.1 should not produce 511)
        if candidate in [421, 450, 451, 452, 550, 551, 552, 553, 554]:
            smtp_code = candidate
            break

    return dsn_code, smtp_code


def classify_bounce(
    dsn_code: Optional[str] = None,
    smtp_code: Optional[int] = None,
    diagnostic_text: Optional[str] = None,
) -> BounceClassification:
    """
    Authoritative bounce/DSN classification function (§7.5, §13.3).

    Rules priority:
    1. THROTTLE:
       - DSN 4.7.x or 4.4.5, or SMTP 421, or throttle keywords.
       - Action: slow send rate, requeue, not a bounce. Do NOT suppress.
    2. POLICY BLOCK:
       - DSN 5.7.1, 5.7.0, 5.7.25, 5.7.26, or spam/reputation keywords.
       - Action: deliverability alarm. CRITICAL: do NOT suppress recipient!
    3. HARD BOUNCE:
       - DSN 5.1.1, 5.1.2, 5.1.3, 5.1.6, 5.2.1, or SMTP 550/551/553 with recipient unknown keywords.
       - Action: permanent suppression, no retry.
    4. SOFT BOUNCE:
       - DSN 4.2.2, 4.3.x, 4.4.x, or mailbox full keywords.
       - Action: retry with backoff, then suppress if max attempts reached.
    """
    text_lower = (diagnostic_text or "").lower()

    # If codes were not supplied, extract from text
    extracted_dsn, extracted_smtp = extract_codes_from_text(diagnostic_text or "")
    effective_dsn = dsn_code or extracted_dsn
    effective_smtp = smtp_code or extracted_smtp

    # Normalize DSN string
    dsn = effective_dsn.strip() if effective_dsn else ""

    # 1. THROTTLE CHECK (§13.3)
    # Check 4.7.x specifically (rate limiting / temporary deferral) or 4.4.5
    is_dsn_throttle = dsn.startswith("4.7.") or dsn == "4.4.5"
    has_throttle_keyword = any(kw in text_lower for kw in THROTTLE_KEYWORDS)
    is_smtp_throttle = effective_smtp == 421 and not any(kw in text_lower for kw in POLICY_KEYWORDS)

    if is_dsn_throttle or is_smtp_throttle or has_throttle_keyword:
        return BounceClassification(
            category=BounceCategory.THROTTLE,
            dsn_code=effective_dsn,
            smtp_code=effective_smtp,
            description="Rate limit or temporary deferral from recipient provider",
            action="slow_send_rate_and_requeue",
            permanent_suppress=False,
            should_retry=True,
            raise_alarm=False,
            slow_send_rate=True,
            is_bounce=False,
        )

    # 2. POLICY / REPUTATION BLOCK CHECK (§13.3)
    # DSN 5.7.1, 5.7.0, or spam / blocklist refusal
    is_dsn_policy = dsn in ["5.7.1", "5.7.0", "5.7.25", "5.7.26"] or (dsn.startswith("5.7.") and not dsn.startswith("5.7.13"))
    has_policy_keyword = any(kw in text_lower for kw in POLICY_KEYWORDS)
    is_smtp_policy = effective_smtp in [554] or (effective_smtp == 550 and has_policy_keyword)

    if is_dsn_policy or has_policy_keyword or is_smtp_policy:
        return BounceClassification(
            category=BounceCategory.POLICY_BLOCK,
            dsn_code=effective_dsn,
            smtp_code=effective_smtp,
            description="Policy or reputation block (spam refusal, IP blocklist, DMARC)",
            action="deliverability_alarm",
            permanent_suppress=False,  # CRITICAL: Do NOT suppress recipient
            should_retry=False,
            raise_alarm=True,
            slow_send_rate=False,
            is_bounce=True,
        )

    # 3. HARD BOUNCE CHECK (§7.5, §13.3)
    # 5.1.1 (user unknown), 5.1.2 (bad destination system), 5.2.1 (mailbox disabled)
    is_dsn_hard = dsn in ["5.1.1", "5.1.2", "5.1.3", "5.1.6", "5.2.1"] or dsn.startswith("5.1.")
    has_hard_keyword = any(kw in text_lower for kw in HARD_BOUNCE_KEYWORDS)
    is_smtp_hard = effective_smtp in [550, 551, 553] and not has_policy_keyword

    if is_dsn_hard or has_hard_keyword or is_smtp_hard:
        return BounceClassification(
            category=BounceCategory.HARD,
            dsn_code=effective_dsn,
            smtp_code=effective_smtp,
            description="Permanent mailbox failure (user unknown, invalid domain, mailbox disabled)",
            action="permanent_suppression",
            permanent_suppress=True,
            should_retry=False,
            raise_alarm=False,
            slow_send_rate=False,
            is_bounce=True,
        )

    # 4. SOFT BOUNCE CHECK (§7.5, §13.3)
    # 4.2.2 (mailbox full), 4.3.x (system status), 4.4.x (transient network), or 5.2.2 (over quota)
    is_dsn_soft = (
        dsn in ["4.2.2", "5.2.2"]
        or dsn.startswith("4.2.")
        or dsn.startswith("4.3.")
        or dsn.startswith("4.4.")
    )
    has_soft_keyword = any(kw in text_lower for kw in SOFT_BOUNCE_KEYWORDS)
    is_smtp_soft = effective_smtp in [450, 451, 452]

    if is_dsn_soft or has_soft_keyword or is_smtp_soft:
        return BounceClassification(
            category=BounceCategory.SOFT,
            dsn_code=effective_dsn,
            smtp_code=effective_smtp,
            description="Transient mailbox failure (mailbox full, system storage, temporary error)",
            action="retry_with_backoff",
            permanent_suppress=False,
            should_retry=True,
            raise_alarm=False,
            slow_send_rate=False,
            is_bounce=True,
        )

    # 5. UNKNOWN FALLBACK
    return BounceClassification(
        category=BounceCategory.UNKNOWN,
        dsn_code=effective_dsn,
        smtp_code=effective_smtp,
        description=f"Unrecognized DSN/SMTP error code: dsn={effective_dsn}, smtp={effective_smtp}",
        action="human_review",
        permanent_suppress=False,
        should_retry=False,
        raise_alarm=True,
        slow_send_rate=False,
        is_bounce=True,
    )
