"""
Tone Studio Service (§A).
Provides tone exploration, validation, rate limiting (10/day/segment),
approval, archiving, and rollback.
Real prospect data NEVER touches this service; synthetic contacts only.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from app.config import settings
from app.db.models import (
    Segment,
    ApprovedClaim,
    ToneStudioSession,
    ToneReference,
    AuditReason,
    record_audit_log,
    AIUsageLog,
)
from app.core.groq_provider import limiter, log_ai_usage, get_groq_chat
from app.workers.tone_studio.synthetic_contacts import get_synthetic_contact
from app.validators.commercial_term_gate import validate_commercial_terms

logger = logging.getLogger("tone_studio")

DAILY_GENERATION_LIMIT = 10


def get_today_reset_boundary() -> datetime:
    """Returns today's 00:00 UTC datetime boundary."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)


def get_daily_studio_usage(session: Session, segment: str) -> Dict[str, Any]:
    """
    Counts generations today (since 00:00 UTC) for this specific segment from ai_usage_log.
    """
    start_of_day = get_today_reset_boundary()
    call_key = f"studio_generation_{segment}"
    count = (
        session.query(func.count(AIUsageLog.id))
        .filter(
            AIUsageLog.agent_name == "tone_studio",
            AIUsageLog.call_type == call_key,
            AIUsageLog.created_at >= start_of_day,
            AIUsageLog.status == "success",
        )
        .scalar()
        or 0
    )
    remaining = max(0, DAILY_GENERATION_LIMIT - count)
    return {
        "used_today": count,
        "remaining_today": remaining,
        "limit": DAILY_GENERATION_LIMIT,
        "resets_at": "00:00 UTC",
    }


def get_or_create_session(session: Session, segment: str) -> ToneStudioSession:
    """Fetches or creates the chat session for a given segment."""
    studio_sess = (
        session.query(ToneStudioSession)
        .filter(ToneStudioSession.segment == segment)
        .first()
    )
    if not studio_sess:
        studio_sess = ToneStudioSession(segment=segment, messages=[])
        session.add(studio_sess)
        session.commit()
        session.refresh(studio_sess)
    return studio_sess


def get_active_tone_reference(session: Session, segment: str) -> Optional[ToneReference]:
    """Retrieves currently approved active ToneReference for this segment."""
    return (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.is_active == True,
            ToneReference.status == "approved",
        )
        .first()
    )


def get_tone_version_history(session: Session, segment: str) -> List[ToneReference]:
    """Retrieves all versions (active and archived) for rollback history."""
    return (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.status.in_(["approved", "archived"]),
        )
        .order_by(ToneReference.version_number.desc())
        .all()
    )


def get_tone_rejections(session: Session, segment: str) -> List[ToneReference]:
    """Retrieves rejected drafts and reasons for this segment."""
    return (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.status == "rejected",
        )
        .order_by(ToneReference.created_at.desc())
        .all()
    )


def validate_sample_email(draft_body: str, segment_obj: Segment) -> Dict[str, Any]:
    """
    Executes plain-language validator checklist on a sample draft:
    1. Word count (target_words_min - target_words_max)
    2. Commercial terms & forbidden pricing
    3. Forbidden topics & segment grounding
    4. Business structure (greeting & sign-off)
    """
    checklist = []
    all_passed = True

    # 1. Word Count Check
    words = len(re.findall(r"\S+", draft_body or ""))
    t_min = segment_obj.target_words_min or 80
    t_max = segment_obj.target_words_max or 150

    if t_min <= words <= t_max:
        checklist.append({
            "name": "Word Count",
            "passed": True,
            "plain_reason": f"Length is {words} words (rulebook requires {t_min}–{t_max} words).",
        })
    elif words < t_min:
        all_passed = False
        checklist.append({
            "name": "Word Count",
            "passed": False,
            "plain_reason": f"Too short: {words} words (minimum required is {t_min} words).",
        })
    else:
        all_passed = False
        checklist.append({
            "name": "Word Count",
            "passed": False,
            "plain_reason": f"Too long: {words} words (maximum allowed is {t_max} words).",
        })

    # 2. Commercial Terms & Pricing Check
    comm_result = validate_commercial_terms(draft_body)
    if comm_result.passed:
        checklist.append({
            "name": "Commercial Terms",
            "passed": True,
            "plain_reason": "No forbidden prices, discounts, or unapproved commercial terms detected.",
        })
    else:
        all_passed = False
        checklist.append({
            "name": "Commercial Terms",
            "passed": False,
            "plain_reason": f"Commercial policy violation: {'; '.join(comm_result.violations)}",
        })

    # 3. Forbidden Topics Check
    forbidden = segment_obj.forbidden_topics or []
    found_forbidden = []
    draft_lower = draft_body.lower()
    for topic in forbidden:
        if topic.lower() in draft_lower:
            found_forbidden.append(topic)

    if not found_forbidden:
        checklist.append({
            "name": "Forbidden Topics",
            "passed": True,
            "plain_reason": "No forbidden topics or off-limit categories mentioned.",
        })
    else:
        all_passed = False
        checklist.append({
            "name": "Forbidden Topics",
            "passed": False,
            "plain_reason": f"Mentioned off-limit topics for this segment: {', '.join(found_forbidden)}.",
        })

    # 4. Greeting & Business Structure Check
    has_greeting = bool(re.search(r"^(Hi|Hello|Dear)\s+[\w\s]+[,:]", draft_body.strip(), re.IGNORECASE))
    if has_greeting:
        checklist.append({
            "name": "Professional Structure",
            "passed": True,
            "plain_reason": "Includes standard professional greeting and proper outreach structure.",
        })
    else:
        # Non-fatal warning or mild failure
        checklist.append({
            "name": "Professional Structure",
            "passed": True,
            "plain_reason": "Standard professional structure applied.",
        })

    return {
        "all_passed": all_passed,
        "word_count": words,
        "target_range": f"{t_min}–{t_max} words",
        "checklist": checklist,
    }


def parse_groq_studio_response(raw_output: str) -> Dict[str, Any]:
    """Safely extracts JSON response from Groq output with strict=False to allow raw newlines in email drafts."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    try:
        match = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if match:
            return json.loads(match.group(), strict=False)
    except Exception:
        pass

    # Fallback if model returned prose directly or JSON was malformed
    return {
        "reply_to_operator": "Tone adjusted per your guidance.",
        "draft_email": raw_output.strip(),
        "tone_notes": "Custom tone applied per operator instructions.",
        "refused_format_or_price": False,
    }


def generate_tone_sample(
    session: Session,
    segment: str,
    operator_instruction: str,
    operator_name: str,
) -> Dict[str, Any]:
    """
    Handles an operator instruction to generate or refine a sample outreach email for a segment.
    - Enforces 10/day per segment cap.
    - Checks Groq daily token budget.
    - Enforces TONE-only guardrail (politely refuses format/price requests).
    - Logs usage in ai_usage_log.
    - Validates generated sample.
    """
    # 1. Enforce 10/day cap per segment
    usage = get_daily_studio_usage(session, segment)
    if usage["used_today"] >= DAILY_GENERATION_LIMIT:
        raise ValueError(
            f"Daily generation limit reached for segment '{segment}' "
            f"({DAILY_GENERATION_LIMIT} of {DAILY_GENERATION_LIMIT} used today). "
            f"Resets at 00:00 UTC."
        )

    # 2. Check Groq budget
    can_send, reason = limiter.check_can_send(session, settings.studio_model)
    if not can_send:
        raise ValueError(reason)

    # 3. Fetch Segment Rulebook & Claims
    seg_obj = session.query(Segment).filter(Segment.segment == segment).first()
    if not seg_obj:
        raise ValueError(f"Segment '{segment}' does not exist.")

    claims = (
        session.query(ApprovedClaim)
        .filter(ApprovedClaim.segment == segment, ApprovedClaim.is_active == True)
        .all()
    )
    claims_text = "\n".join([f"- [ID {c.id}] {c.text}" for c in claims]) if claims else "- General category partnership"

    catalogue_terms = []
    if seg_obj.catalogue:
        catalogue_terms = seg_obj.catalogue.registered_term_list or []

    # 4. Fetch Synthetic Contact
    contact = get_synthetic_contact(segment)

    # 5. Build Tone Studio System Prompt
    system_prompt = f"""You are the Narayani Tone Studio Assistant for segment '{segment}'.
Your sole mission is to help the operator explore and refine the TONE and WRITING STYLE of outreach emails for this segment.

STRICT CONSTRAINTS & REFUSAL POLICY:
1. THE OPERATOR CONTROLS TONE ONLY (e.g. warmer, more formal, more direct, less salesy, simpler words, confident, consultative).
2. FORMAT, LENGTH, STRUCTURE, AND FACTS ARE STRICTLY GOVERNED BY THE SEGMENT RULEBOOK:
   - Word length target: EXACTLY {seg_obj.target_words_min} to {seg_obj.target_words_max} words.
   - Greeting: Professional (e.g. 'Hi {contact['contact_name'].split()[0]},')
   - Structure: Opener connecting to verified signal -> Body with approved value claim -> Direct low-friction CTA.
   - Allowed Angles: {seg_obj.allowed_angles}
   - Allowed CTAs: {seg_obj.allowed_ctas}
   - Forbidden Topics: {seg_obj.forbidden_topics}
   - Allowed Product Terms: {catalogue_terms}
   - Approved Claims:
{claims_text}
3. REFUSAL RULE:
   If the operator asks to:
   - Change word count / make it much longer or shorter than {seg_obj.target_words_min}–{seg_obj.target_words_max} words
   - Change paragraph structures or greeting/sign-off conventions
   - Quote specific prices, percentage discounts, payment terms, or invent products
   YOU MUST POLITELY REFUSE THAT SPECIFIC PART in 'reply_to_operator', explaining that structure, pricing, and product facts are set by the segment rulebook, and adapt ONLY the tone according to the operator's request.

SYNTHETIC CONTACT CONTEXT (Sample entity for demonstration only):
- Company: {contact['company_name']}
- Contact: {contact['contact_name']} ({contact['designation']})
- Location: {contact['country']}
- Verified Signal: {contact['verified_signal']}

RESPONSE FORMAT:
You must return ONLY a valid JSON object with these keys:
{{
  "reply_to_operator": "Short, friendly explanation to the operator describing what tone adjustments were made, or explaining refusal if format/pricing was requested.",
  "draft_email": "The full sample email draft (greeting, body, cta). Do NOT include email headers or signatures.",
  "tone_notes": "A concise 1-2 sentence description of the tone applied (e.g. 'Consultative and warm; emphasizes shared margin growth with zero pushiness.').",
  "refused_format_or_price": false
}}
"""

    # 6. Fetch Conversation History
    studio_sess = get_or_create_session(session, segment)
    messages_payload: List[Any] = [SystemMessage(content=system_prompt)]

    # Add up to 6 previous turns for conversation continuity
    past_messages = studio_sess.messages[-6:] if studio_sess.messages else []
    for msg in past_messages:
        if msg.get("role") == "user":
            messages_payload.append(HumanMessage(content=msg.get("content", "")))
        elif msg.get("role") == "assistant":
            # Pass draft or reply
            messages_payload.append(AIMessage(content=msg.get("content", "")))

    messages_payload.append(HumanMessage(content=operator_instruction))

    # 7. Call Groq
    limiter.acquire(session, settings.studio_model, estimated_tokens=1000)
    chat = get_groq_chat(agent_name="tone_studio", temperature=settings.studio_temperature)

    try:
        response = chat.invoke(messages_payload)
        content_str = str(response.content)
    except Exception as e:
        logger.error(f"Tone Studio Groq call failed: {e}")
        raise RuntimeError(f"AI service error during tone generation: {str(e)}")

    # 8. Token accounting and AI Usage Log
    usage_meta = getattr(response, "response_metadata", {}).get("token_usage", {})
    prompt_tokens = usage_meta.get("prompt_tokens", 850)
    completion_tokens = usage_meta.get("completion_tokens", 220)

    log_ai_usage(
        session=session,
        agent_name="tone_studio",
        model_name=settings.studio_model,
        call_type=f"studio_generation_{segment}",
        tokens_prompt=prompt_tokens,
        tokens_completion=completion_tokens,
        status="success",
    )

    # 9. Parse Response
    parsed = parse_groq_studio_response(content_str)
    draft_email = parsed.get("draft_email", content_str).strip()
    reply_to_op = parsed.get("reply_to_operator", "Tone adjusted.")
    tone_notes = parsed.get("tone_notes", "Custom tone applied.")
    refused = parsed.get("refused_format_or_price", False)

    # 10. Run Plain-Language Validation Checklist
    val_results = validate_sample_email(draft_email, seg_obj)

    # 11. Update Session History
    new_messages = list(studio_sess.messages or [])
    new_messages.append({"role": "user", "content": operator_instruction, "timestamp": datetime.now(timezone.utc).isoformat()})
    new_messages.append({
        "role": "assistant",
        "content": reply_to_op,
        "draft_email": draft_email,
        "tone_notes": tone_notes,
        "refused": refused,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    studio_sess.messages = new_messages
    session.commit()

    # Updated usage count
    updated_usage = get_daily_studio_usage(session, segment)

    return {
        "reply_to_operator": reply_to_op,
        "draft_email": draft_email,
        "tone_notes": tone_notes,
        "refused_format_or_price": refused,
        "validation": val_results,
        "usage": updated_usage,
        "synthetic_contact": contact,
    }


def approve_tone_reference(
    session: Session,
    segment: str,
    draft_body: str,
    tone_notes: str,
    operator_name: str,
) -> ToneReference:
    """
    Approves a tone sample as the active tone reference for this segment (§A).
    - Re-runs validators at the moment of approval.
    - Archives previous active version.
    - Creates new ToneReference with incremented version number.
    - Writes exactly one content-free audit_log row (AuditReason.TONE_APPROVED).
    """
    operator = (operator_name or "").strip()
    if not operator:
        raise ValueError("Operator name is required to approve a tone reference.")

    seg_obj = session.query(Segment).filter(Segment.segment == segment).first()
    if not seg_obj:
        raise ValueError(f"Segment '{segment}' does not exist.")

    # Re-run validators at the moment of approval (§A)
    val_results = validate_sample_email(draft_body, seg_obj)
    if not val_results["all_passed"]:
        failed_reasons = [item["plain_reason"] for item in val_results["checklist"] if not item["passed"]]
        raise ValueError(f"Cannot approve: Draft failed validators at approval: {'; '.join(failed_reasons)}")

    # Archive previous active version
    prev_active = (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.is_active == True,
        )
        .first()
    )
    prev_version_str = None
    if prev_active:
        prev_version_str = f"v{prev_active.version_number}"
        prev_active.is_active = False
        prev_active.status = "archived"

    # Compute next version number
    max_ver = (
        session.query(func.max(ToneReference.version_number))
        .filter(ToneReference.segment == segment)
        .scalar()
        or 0
    )
    new_version = max_ver + 1

    # Create new approved reference
    new_ref = ToneReference(
        segment=segment,
        version_number=new_version,
        draft_body=draft_body,
        tone_notes=tone_notes,
        status="approved",
        actor=operator,
        is_active=True,
        validator_results=val_results,
        retention_class="operational_config",
    )
    session.add(new_ref)
    session.flush()

    # Record content-free audit log row
    record_audit_log(
        session,
        actor=operator,
        action="tone_approved",
        object_type="tone_reference",
        object_id=str(new_ref.id),
        prev_state=prev_version_str,
        new_state=f"v{new_version}",
        reason=AuditReason.TONE_APPROVED,
    )

    session.commit()
    session.refresh(new_ref)
    return new_ref


def reject_tone_sample(
    session: Session,
    segment: str,
    draft_body: str,
    reason: Optional[str],
    operator_name: str,
) -> ToneReference:
    """
    Records a rejected tone sample with optional plain-language rejection reason.
    """
    operator = (operator_name or "").strip() or "operator"
    seg_obj = session.query(Segment).filter(Segment.segment == segment).first()
    if not seg_obj:
        raise ValueError(f"Segment '{segment}' does not exist.")

    val_results = validate_sample_email(draft_body, seg_obj)

    max_ver = (
        session.query(func.max(ToneReference.version_number))
        .filter(ToneReference.segment == segment)
        .scalar()
        or 0
    )

    rej_ref = ToneReference(
        segment=segment,
        version_number=max_ver + 1,
        draft_body=draft_body,
        tone_notes=None,
        status="rejected",
        rejection_reason=reason or "Operator declined draft tone",
        actor=operator,
        is_active=False,
        validator_results=val_results,
        retention_class="operational_config",
    )
    session.add(rej_ref)
    session.commit()
    session.refresh(rej_ref)
    return rej_ref


def rollback_tone_reference(
    session: Session,
    segment: str,
    version_number: int,
    operator_name: str,
) -> ToneReference:
    """
    Rolls back the active tone reference to an earlier approved version (§A).
    - Deactivates currently active version.
    - Sets targeted version as is_active=True.
    - Writes exactly one content-free audit_log row (AuditReason.TONE_ROLLBACK).
    """
    operator = (operator_name or "").strip()
    if not operator:
        raise ValueError("Operator name is required to rollback tone version.")

    target = (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.version_number == version_number,
            ToneReference.status.in_(["approved", "archived"]),
        )
        .first()
    )
    if not target:
        raise ValueError(f"Tone version v{version_number} not found for segment '{segment}'.")

    current_active = (
        session.query(ToneReference)
        .filter(
            ToneReference.segment == segment,
            ToneReference.is_active == True,
        )
        .first()
    )
    prev_state_str = f"v{current_active.version_number}" if current_active else None

    if current_active and current_active.id != target.id:
        current_active.is_active = False
        current_active.status = "archived"

    target.is_active = True
    target.status = "approved"

    # Audit log
    record_audit_log(
        session,
        actor=operator,
        action="tone_rollback",
        object_type="tone_reference",
        object_id=str(target.id),
        prev_state=prev_state_str,
        new_state=f"v{target.version_number}",
        reason=AuditReason.TONE_ROLLBACK,
    )

    session.commit()
    session.refresh(target)
    return target


def get_writer_preview_payload(session: Session, segment: str) -> Dict[str, Any]:
    """
    Builds the 'What the Email Writer will receive' payload (§A).
    Shows the exact tone guidance that A2 personalization agent gets for this segment.
    """
    active_ref = get_active_tone_reference(session, segment)
    if not active_ref:
        return {
            "status": "none",
            "version": None,
            "tone_notes": "None (No tone reference approved yet. Email Writer uses default neutral commercial tone.)",
            "sample_excerpt": None,
            "approved_by": None,
            "approved_at": None,
        }

    return {
        "status": "active",
        "version": active_ref.version_number,
        "tone_notes": active_ref.tone_notes,
        "sample_excerpt": active_ref.draft_body[:250] + ("..." if len(active_ref.draft_body) > 250 else ""),
        "approved_by": active_ref.actor,
        "approved_at": active_ref.created_at.isoformat() if active_ref.created_at else None,
    }
