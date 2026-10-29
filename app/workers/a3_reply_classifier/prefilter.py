import re
from typing import Dict, Any, Optional
from app.schemas.a3_contract import A3ReplyContract, A3Kind, A3Intent, A3SuppressionSignal

OOO_SUBJECT_REGEX = re.compile(
    r"\b(out of office|ooo|autoreply|auto-reply|automatic reply|away from (the )?office|on vacation|on leave|annual leave|paternity leave|maternity leave|out of the country|automated response)\b",
    re.IGNORECASE,
)

BOUNCE_SUBJECT_REGEX = re.compile(
    r"\b(undeliverable|delivery status notification|delivery failure|failure notice|returned mail|mail delivery failed|mail delivery subsystem)\b",
    re.IGNORECASE,
)


def run_prefilter(
    headers: Optional[Dict[str, str]] = None,
    subject: Optional[str] = None,
) -> Optional[A3ReplyContract]:
    """
    Deterministic pre-filter running BEFORE any LLM call (VISION.md §7.5).
    Catches auto-replies and bounces via:
      - Auto-Submitted header
      - X-Autoreply header
      - Precedence: bulk/junk/list/auto_reply
      - DSN content-type
      - Out-of-Office and bounce subject patterns
    Catches ~70% of auto-replies at 0 LLM token cost.
    """
    headers = headers or {}
    normalized_headers = {k.lower().strip(): str(v).lower().strip() for k, v in headers.items()}
    subj = (subject or "").strip()

    # 1. Check DSN / Bounce content-type & headers
    content_type = normalized_headers.get("content-type", "")
    if "report-type=delivery-status" in content_type or "message/delivery-status" in content_type:
        return A3ReplyContract(
            kind=A3Kind.BOUNCE_NOTICE,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary="Pre-filter: DSN content-type detected",
        )

    if "x-failed-recipients" in normalized_headers:
        return A3ReplyContract(
            kind=A3Kind.BOUNCE_NOTICE,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary="Pre-filter: X-Failed-Recipients header detected",
        )

    # 2. Check Bounce Subject pattern
    if BOUNCE_SUBJECT_REGEX.search(subj):
        return A3ReplyContract(
            kind=A3Kind.BOUNCE_NOTICE,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary=f"Pre-filter: Bounce subject detected ('{subj}')",
        )

    # 3. Check Auto-Submitted header
    auto_submitted = normalized_headers.get("auto-submitted", "")
    if auto_submitted and auto_submitted != "no":
        return A3ReplyContract(
            kind=A3Kind.AUTO_REPLY,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary=f"Pre-filter: Auto-Submitted header '{auto_submitted}'",
        )

    # 4. Check X-Autoreply header
    x_autoreply = normalized_headers.get("x-autoreply", "")
    if x_autoreply in ["yes", "true", "1", "auto-reply"]:
        return A3ReplyContract(
            kind=A3Kind.AUTO_REPLY,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary="Pre-filter: X-Autoreply header present",
        )

    # 5. Check Precedence header
    precedence = normalized_headers.get("precedence", "")
    if precedence in ["bulk", "junk", "list", "auto_reply"]:
        return A3ReplyContract(
            kind=A3Kind.AUTO_REPLY,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary=f"Pre-filter: Precedence '{precedence}'",
        )

    # 6. Check Out-of-Office Subject pattern
    if OOO_SUBJECT_REGEX.search(subj):
        return A3ReplyContract(
            kind=A3Kind.AUTO_REPLY,
            intent=A3Intent.UNKNOWN,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=1.0,
            summary=f"Pre-filter: Out-of-Office subject detected ('{subj}')",
        )

    # No pre-filter match -> proceed to LLM
    return None
