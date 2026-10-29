"""
Reason Formatter for Narayani Outreach Pipeline (Phase 5).

Translates technical validator outputs, hold codes, vetoes, and provider issues
into clear, non-technical plain language for the human operator.
Strictly eliminates raw codes (e.g. VALIDATOR_HOLD, exhausted_repairs, check_h2).
Provides a plain-language summary, failed validator label, and a one-line "What should I do?" action hint.
"""

import re
from typing import Dict, Any, List, Optional, Tuple


VALIDATOR_LABELS = {
    "rulebook": "Rulebook Guidelines",
    "word_count": "Word Count Range",
    "greeting": "Greeting Format",
    "catalogue_grounding": "Catalogue Grounding",
    "grounding": "Catalogue Grounding",
    "sameness": "Sameness & Repetition",
    "segment_conformance": "Segment Conformance",
    "contract_gate": "Contract Conformance",
    "commercial_terms": "Commercial Term Gate",
    "forbidden_topics": "Segment Topic Gate",
    "veto": "Prospect Fit Veto",
    "ai_service": "AI Service Availability",
}


def clean_raw_error_message(err: str) -> str:
    """Removes technical prefixes, codes, and regex tokens from raw validator error strings."""
    if not err:
        return ""
    text = str(err).strip()

    # Replace raw technical codes with plain terms
    text = re.sub(r"\bVALIDATOR_HOLD\b", "quality hold", text, flags=re.IGNORECASE)
    text = re.sub(r"\bexhausted_repairs\b", "repairs exhausted", text, flags=re.IGNORECASE)
    text = re.sub(r"\bno_progress_detector\b", "no repair progress", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcta_or_angle_unknown\b", "call-to-action unknown", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcheck_[a-z0-9]+\b", "", text, flags=re.IGNORECASE)

    # Strip technical check prefixes
    text = re.sub(r"^Check\s*\([a-z0-9_]+\)\s*(failed:)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(h1|h2)\s*failed:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Check\s*\(h2\)\s*Grounding violation:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^Validator\s*hold\s*\(\d+\s*attempts:\s*", "", text, flags=re.IGNORECASE)
    if text.endswith(")"):
        text = text[:-1].strip()

    return text.strip()


def format_error_detail(err: str) -> Tuple[str, str, str]:
    """
    Analyzes an individual error string.
    Returns (plain_description, validator_key, suggested_action).
    """
    cleaned = clean_raw_error_message(err)
    lowered = cleaned.lower()

    # 1. Banned product terms from other catalogues (e.g. snacks, mixes)
    if "banned product terms from other catalogues" in lowered or "outside catalogue" in lowered:
        # Extract quoted terms if present
        terms_match = re.findall(r"'([^']+)'", cleaned)
        banned_terms = [t for t in terms_match if t not in ("catalogue", "segment")]
        if banned_terms:
            terms_str = ", ".join(f"'{t}'" for t in banned_terms)
            return (
                f"The email mentioned {terms_str}, which is not approved for this customer type.",
                "catalogue_grounding",
                "Click 'Edit & Approve' to replace with an approved product from this segment's catalogue, or dismiss.",
            )
        return (
            "The email mentioned products not approved for this customer type.",
            "catalogue_grounding",
            "Click 'Edit & Approve' to replace with an approved product from this segment's catalogue, or dismiss.",
        )

    # 2. Commercial terms (prices, MOQs, currencies, payment terms)
    if "commercial term" in lowered or "commercial offer" in lowered or "unit-free" in lowered:
        return (
            "The email mentioned commercial terms, pricing, or minimum order quantities.",
            "commercial_terms",
            "Click 'Edit & Approve' to remove pricing, payment terms, or MOQs, or dismiss.",
        )

    # 3. Tone Studio copy violation
    if "tone reference copy violation" in lowered or "tone studio approved sample" in lowered:
        return (
            "The draft copy was too similar to the Tone Studio approved sample reference.",
            "sameness",
            "Click 'Edit & Approve' to reword the draft so it expresses the tone in original words, or retry.",
        )

    # 4. Word count limits
    if "word count" in lowered and "outside" in lowered:
        m = re.search(r"word count (\d+) is outside the allowed range \[(\d+),\s*(\d+)\]", cleaned, re.IGNORECASE)
        if m:
            actual, wmin, wmax = m.group(1), m.group(2), m.group(3)
            return (
                f"Email length is {actual} words, which is outside the segment target of {wmin} to {wmax} words.",
                "word_count",
                "Click 'Edit & Approve' to trim or expand text to meet the target length, or retry.",
            )
        return (
            "Email length is outside the segment target range.",
            "word_count",
            "Click 'Edit & Approve' to adjust text length, or retry.",
        )

    # 5. Greeting format
    if "greeting" in lowered:
        return (
            "Opening greeting format does not match rulebook guidelines (must be 'Hi <FirstName>,').",
            "greeting",
            "Click 'Edit & Approve' to correct line 1 to 'Hi <FirstName>,', or retry.",
        )

    # 6. Banned openers / phrases
    if "banned opener" in lowered or "banned phrase" in lowered:
        m = re.search(r":\s*'([^']+)'", cleaned)
        phrase = m.group(1) if m else "generic sales phrase"
        return (
            f"Contains banned phrase '{phrase}', which is forbidden under Narayani guidelines.",
            "rulebook",
            "Click 'Edit & Approve' to remove or replace the forbidden phrase, or retry.",
        )

    # 7. Exclamation mark or ALL-CAPS
    if "exclamation" in lowered:
        return (
            "Contains an exclamation mark, which is forbidden in Narayani professional outreach.",
            "rulebook",
            "Click 'Edit & Approve' to remove exclamation marks, or retry.",
        )
    if "all-caps" in lowered or "all caps" in lowered:
        return (
            "Contains ALL-CAPS words, which are forbidden under rulebook guidelines.",
            "rulebook",
            "Click 'Edit & Approve' to use normal capitalization, or retry.",
        )

    # 8. Paragraph and sentence limits
    if "paragraph" in lowered or "sentence" in lowered:
        return (
            "Paragraph structure or sentence count exceeds the rulebook limit (max 3 paragraphs, 2-3 sentences each).",
            "rulebook",
            "Click 'Edit & Approve' to format into 2-3 sentence paragraphs, or retry.",
        )

    # 9. Sameness with prior messages / openers
    if "opening sentence" in lowered and ("hash" in lowered or "similarity" in lowered):
        return (
            "Opening sentence repeats a previously used opener from a recent outreach email.",
            "sameness",
            "Click 'Edit & Approve' to write a fresh opener, or retry.",
        )
    if "similarity" in lowered or "avoided phrase" in lowered:
        return (
            "Draft text is too similar to recently sent emails in this segment.",
            "sameness",
            "Click 'Edit & Approve' to customize opening and body text, or retry.",
        )

    # 10. Segment Conformance (CTA or Claim)
    if "cta" in lowered or "claim" in lowered or "cta_or_angle_unknown" in lowered:
        return (
            "The chosen call-to-action or angle is not approved for this customer segment.",
            "segment_conformance",
            "Click 'Edit & Approve' to select an approved call-to-action, or retry.",
        )

    # 11. Forbidden topic
    if "forbidden topic" in lowered:
        return (
            "The email mentions topics restricted for this customer segment.",
            "forbidden_topics",
            "Click 'Edit & Approve' to remove restricted topics, or dismiss.",
        )

    # 12. AI provider issue
    if "ai service" in lowered or "groq" in lowered or "429" in lowered or "rate limit" in lowered:
        return (
            "AI service temporarily busy; system paused and will retry automatically.",
            "ai_service",
            "No action required; the system will automatically retry without attempt penalty.",
        )

    # 13. Veto / Skip
    if "veto" in lowered or "skip" in lowered or "does not fit" in lowered:
        return (
            f"This business does not fit the customer segment ({cleaned}).",
            "veto",
            "Review prospect details in Contacts and update segment if appropriate, or dismiss.",
        )

    # Default fallback: clean plain-language sentence
    clean_fallback = cleaned[0].upper() + cleaned[1:] if cleaned else "Quality check did not pass"
    if not clean_fallback.endswith("."):
        clean_fallback += "."
    return (
        clean_fallback,
        "rulebook",
        "Click 'Edit & Approve' to review and refine the email text, or retry.",
    )


def format_attempt_history(raw_history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Converts raw attempt_history records into clear, human-readable entries.
    """
    if not raw_history:
        return []

    formatted_history = []
    for item in raw_history:
        att_num = item.get("attempt", 1)
        raw_val = item.get("failed_validator", "rulebook")
        raw_errors = item.get("errors", [])

        plain_errors = []
        val_key = raw_val
        for err in raw_errors:
            desc, v_key, _ = format_error_detail(str(err))
            plain_errors.append(desc)
            if v_key:
                val_key = v_key

        val_label = VALIDATOR_LABELS.get(val_key, VALIDATOR_LABELS.get(raw_val, "Quality Check"))
        formatted_history.append({
            "attempt": att_num,
            "failed_validator": raw_val,
            "failed_validator_label": val_label,
            "errors": plain_errors if plain_errors else ["Quality check failed on this attempt."],
            "summary": f"Attempt {att_num} of 3: {val_label} ({plain_errors[0] if plain_errors else 'Check failed'})",
        })

    return formatted_history


def format_hold_reason(
    hold_reason: Optional[str],
    failed_validator: Optional[str] = None,
    attempts: int = 1,
    status: Optional[str] = None,
    attempt_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Generates plain-language label, failure description, and one-line 'What should I do?' action hint.

    Returns:
        {
            "plain_label": str,             # e.g. "Held (3 of 3 attempts)" or "Skipped (Veto)"
            "plain_reason": str,            # e.g. "Held: the email mentioned 'snacks', which is not approved for this customer type (3 of 3 attempts)"
            "failed_validator_label": str,  # e.g. "Catalogue Grounding"
            "action_hint": str,             # e.g. "Click 'Edit & Approve' to replace with an approved product, or dismiss."
            "is_veto": bool,
            "is_paused": bool,
            "attempt_history": List[Dict],
        }
    """
    raw_reason = (hold_reason or "").strip()
    stat_lower = (status or "").lower()

    # 0. Ambiguous Delivery Reconciliation Hold
    if "sent folder" in raw_reason.lower():
        return {
            "plain_label": "Held (Verify Sent Folder)",
            "plain_reason": "We can't tell whether this email was sent. Check your Sent folder.",
            "failed_validator_label": "Delivery Reconciliation",
            "action_hint": "Check your Sent folder to verify whether this message went out before retrying or dismissing.",
            "is_veto": False,
            "is_paused": False,
            "is_dead_letter": False,
            "category_label": "Emails that couldn't be written correctly",
            "attempt_history": format_attempt_history(attempt_history),
        }

    # 1. Dead Letter (Technical failure)
    if stat_lower == "dead_letter" or "dead_letter" in raw_reason.lower() or "technical failure" in raw_reason.lower():
        return {
            "plain_label": "Stuck: needs technical help",
            "plain_reason": raw_reason or "Technical failure: processing failed 3 times (crashes or timeouts)",
            "failed_validator_label": "Technical Failure",
            "action_hint": "Item crashed or timed out 3 times. Check server logs or run a recovery check.",
            "is_veto": False,
            "is_paused": False,
            "is_dead_letter": True,
            "category_label": "Stuck: needs technical help",
            "attempt_history": format_attempt_history(attempt_history),
        }

    # 2. AI Service Busy / Paused
    if (
        stat_lower in ("waiting_for_ai", "paused_daily_tokens_exhausted")
        or "ai service" in raw_reason.lower()
        or "groq" in raw_reason.lower()
        or "waiting for the ai service" in raw_reason.lower()
    ):
        return {
            "plain_label": "Paused (AI Busy)",
            "plain_reason": "Paused: Groq was busy, will retry automatically",
            "failed_validator_label": "AI Service Availability",
            "action_hint": "No action needed; the system will retry automatically without attempt penalty.",
            "is_veto": False,
            "is_paused": True,
            "is_dead_letter": False,
            "category_label": "Emails that couldn't be written correctly",
            "attempt_history": format_attempt_history(attempt_history),
        }

    # 3. Veto / Skipped
    if stat_lower == "vetoed" or (raw_reason and "veto" in raw_reason.lower()):
        clean_msg = raw_reason
        # Strip prefixes
        clean_msg = re.sub(r"^(veto:\s*|skipped\s*\(veto\):\s*)", "", clean_msg, flags=re.IGNORECASE).strip()
        if not clean_msg or clean_msg.lower() in ("veto", "vetoed", "veto_limit"):
            clean_msg = "this business does not fit the segment"
        return {
            "plain_label": "Skipped (Veto)",
            "plain_reason": f"Skipped (veto): {clean_msg}",
            "failed_validator_label": "Prospect Fit Veto",
            "action_hint": "Prospect does not fit segment criteria; review details in Contacts or re-segment if appropriate.",
            "is_veto": True,
            "is_paused": False,
            "is_dead_letter": False,
            "category_label": "Emails that couldn't be written correctly",
            "attempt_history": format_attempt_history(attempt_history),
        }

    # 4. Held Draft
    att_used = max(1, min(3, attempts if attempts > 0 else 1))

    # Inspect attempt_history or hold_reason to get the most specific error
    primary_error = raw_reason
    if attempt_history:
        last_attempt = attempt_history[-1]
        errs = last_attempt.get("errors", [])
        if errs:
            primary_error = str(errs[0])

    desc, val_key, hint = format_error_detail(primary_error)

    # Use explicitly passed failed_validator if present and not refined to specific sub-rules
    if failed_validator and failed_validator in VALIDATOR_LABELS:
        if val_key not in ("word_count", "greeting", "commercial_terms", "forbidden_topics"):
            val_key = failed_validator

    val_label = VALIDATOR_LABELS.get(val_key, "Quality Check")

    # Format plain_reason with attempts count
    desc_clean = desc.rstrip(".")
    plain_reason = f"Held: {desc_clean} ({att_used} of 3 attempts)"

    return {
        "plain_label": f"Held ({att_used} of 3 attempts)",
        "plain_reason": plain_reason,
        "failed_validator_label": val_label,
        "action_hint": hint,
        "is_veto": False,
        "is_paused": False,
        "is_dead_letter": False,
        "category_label": "Emails that couldn't be written correctly",
        "attempt_history": format_attempt_history(attempt_history),
    }

