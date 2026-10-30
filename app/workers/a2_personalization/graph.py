import json
import logging
import re
from datetime import datetime, timezone
from typing import TypedDict, List, Optional, Dict, Any, Tuple
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from app.config import settings
from app.core.groq_provider import (
    get_groq_chat,
    limiter,
    log_ai_usage,
    GroqProviderError,
    extract_retry_after,
)
from app.db.session import get_db_session
from app.validators.grounding import MessageBrief, validate_grounding
from app.validators.rulebook_checks import validate_rulebook_v0
from app.validators.sameness import validate_sameness, hash_sentence, extract_opening_sentence
from app.schemas.a2_contract import A2Output, MessageMetadata

logger = logging.getLogger("a2_graph")


class A2State(TypedDict):
    brief: MessageBrief
    first_name: str
    round: int
    attempt: int
    draft_body: str
    full_email_body: str
    output: Optional[A2Output]
    metadata: Optional[MessageMetadata]
    rulebook_errors: List[str]
    grounding_errors: List[str]
    sameness_errors: List[str]
    segment_conformance_errors: List[str]
    format_errors: List[str]
    previous_errors: List[str]
    status: str  # "pending", "emitted", "held", "vetoed", "format_error"
    hold_reason: Optional[str]
    failed_validator: Optional[str]
    veto_reason: Optional[str]
    attempt_history: List[Dict[str, Any]]
    prospect_recent_messages: Optional[List[str]]
    segment_recent_messages: Optional[List[str]]
    recent_opening_sentences: Optional[List[str]]
    opening_hashes: Optional[List[str]]
    phrases_to_avoid: Optional[List[str]]
    tone_notes: Optional[str]
    tone_sample_reference: Optional[str]
    rulebook_config: Optional[Dict[str, Any]]


A2_SYSTEM_PROMPT_V2 = """You are A2 PersonalizationAgent (Email Writer) for Narayani Distributors B2B Outreach System (VISION.md §7.2).
You compose individually personalized cold outreach emails to business prospects.

HOUSE VOICE & ACTIVE RULEBOOK GUIDANCE:
1. Greeting form: The opener MUST begin with exactly "Hi <First Name>," where <First Name> is the prospect's real first name.
2. Banned openers (NEVER use):
{banned_openers_formatted}
3. Banned phrases (NEVER use):
   {banned_phrases_formatted}
4. Word count: The assembled prose (opener + body + cta) MUST contain between {target_words_min} and {target_words_max} words. Aim comfortably for roughly {target_words_comfort} words across your paragraphs (with ~12-16 words per sentence) so your draft never falls below {target_words_min} words.
5. Paragraph structure: Exactly {max_paragraphs} paragraphs. Each paragraph MUST have 2 to {max_sentences_per_paragraph} complete sentences.
6. Tone & punctuation:
   - Absolutely NO exclamation marks ('!').
   - Absolutely NO ALL-CAPS words (except standard acronyms like FSSAI, ISO, APEDA, EAN, FMCG, B2B, NPOP, or uppercase company names/acronyms).
7. COMMERCIAL RESTRICTION:
   - Categorically DO NOT include any specific prices, discounts, MOQ numbers, shelf-life dates, or delivery days.
   - The CTA must be an offer to share a catalogue, spec sheet, sample pack, or discussion — never a statement of terms.
   - Bare factual numbers (e.g. "Founded 40 years ago in Gujarat", "spans 12 categories") are allowed if grounded.
8. GROUNDING & STRICT CATALOGUE BOUNDARIES:
   - You may ONLY reference products and categories from this authorized list for this contact: {allowed_products_str}.
   - You must NOT mention or compare any product, ingredient, or category term that is not in this authorized list.

TONE REFERENCE GUIDANCE:
{tone_reference_str}

OUTPUT FORMAT SPECIFICATION:
You must return a strictly valid JSON object matching this exact schema:
{{
  "subject": "<subject line under 90 characters>",
  "opener": "Hi <First Name>, <exactly 2 sentences acknowledging business context>",
  "body": "<body prose citing approved claims: if target minimum words >= 85, write exactly 2 paragraphs separated by \\n\\n, each having 2 to {max_sentences_per_paragraph} complete sentences; otherwise 1 to 2 paragraphs of 2 to {max_sentences_per_paragraph} sentences>",
  "cta": "<exactly 2 sentences closing offer or question>",
  "cta_id": "<exact cta identifier chosen from allowed_ctas>",
  "angle": "<exact angle chosen from allowed_angles>",
  "claims_used": [<list of integer claim IDs cited>],
  "product_terms_used": [<list of product terms cited>],
  "confidence": <float between 0.0 and 1.0>,
  "skip_reason": <string veto reason if prospect is unsuitable, otherwise null>
}}
"""


def get_rulebook_footer(brief: MessageBrief) -> str:
    """Builds the rulebook footer dynamically from segment configuration."""
    config = brief.campaign_config or {}
    sender_name = config.get("sender_name", "Aarav Shah")
    sender_title = config.get("sender_title", "B2B Procurement & Exports")
    company_legal_name = config.get("company_legal_name", "Narayani Distributors LLP")
    contact_line = config.get("contact_line", "Ahmedabad, Gujarat | contact@narayanidistributors.com")
    return (
        f"{sender_name}, {sender_title}\n"
        f"{company_legal_name}\n"
        f"{contact_line}\n"
        f"If you'd rather not hear from us, reply STOP and we won't email you again."
    )


def parse_groq_json_response(raw_output: str) -> Dict[str, Any]:
    """Parses JSON cleanly from LLM response with fallback extraction."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", raw_output, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Could not parse valid JSON from LLM response: {raw_output[:200]}")


def parse_a2_response(raw: str) -> Tuple[str, MessageMetadata]:
    """Parses A2 output supporting both legacy delimited format and JSON contract v2."""
    if "---BODY_TEXT---" in raw and "---METADATA---" in raw:
        parts = raw.split("---METADATA---")
        body_part = parts[0].replace("---BODY_TEXT---", "").strip()
        meta_dict = json.loads(parts[1].strip())
        return body_part, MessageMetadata(**meta_dict)
    parsed = parse_groq_json_response(raw)
    out = A2Output(**parsed)
    return out.assemble_prose_for_validation(), MessageMetadata(
        subject=out.subject,
        angle_used=out.angle,
        cta=out.cta,
        claims_used=out.claims_used,
        product_terms_used=out.product_terms_used,
        confidence=out.confidence,
        skip_reason=out.skip_reason,
    )



def call_groq_a2(
    system_prompt: str,
    user_prompt: str,
    attempt: int = 1,
) -> Tuple[Optional[A2Output], int, int, str, List[str]]:
    """
    Calls Groq model with rate limiting, error classification, and 1 free format re-ask.
    Returns (output, prompt_tokens, completion_tokens, status, error_list).
    status: 'ok' or 'format_error'.
    Provider errors (network, timeout, 429, 401, 5xx) raise GroqProviderError.
    """
    with get_db_session() as db:
        limiter.acquire(db, settings.a2_model, estimated_tokens=1400)

    chat = get_groq_chat(agent_name="A2_writer", temperature=settings.a2_temperature)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = chat.invoke(messages)
    except Exception as e:
        if isinstance(e, GroqProviderError):
            raise
        retry_after = extract_retry_after(e)
        logger.error(f"Groq API invocation error: {e}")
        raise GroqProviderError(str(e), original_error=e, retry_after=retry_after)

    content = str(response.content)
    usage_meta = getattr(response, "response_metadata", {}).get("token_usage", {})
    prompt_tokens = usage_meta.get("prompt_tokens", 1100)
    completion_tokens = usage_meta.get("completion_tokens", 250)

    with get_db_session() as db:
        log_ai_usage(
            session=db,
            agent_name="A2_writer",
            model_name=settings.a2_model,
            call_type="repair" if attempt > 1 else "draft",
            tokens_prompt=prompt_tokens,
            tokens_completion=completion_tokens,
            status="success",
        )

    # Attempt to parse output
    try:
        parsed = parse_groq_json_response(content)
        output = A2Output(**parsed)
        return output, prompt_tokens, completion_tokens, "ok", []
    except Exception as parse_err:
        logger.warning(f"Groq format parse failed ({parse_err}). Performing 1 free re-ask...")
        # 1 free re-ask
        reask_prompt = (
            "Your previous response was not valid JSON or did not match the required schema.\n"
            "Output ONLY a raw, strictly valid JSON object matching the exact schema specified in the system prompt. "
            "Do NOT include markdown fences, preambles, or commentary."
        )
        try:
            reask_messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
                response,
                HumanMessage(content=reask_prompt),
            ]
            reask_response = chat.invoke(reask_messages)
            reask_content = str(reask_response.content)
            reask_parsed = parse_groq_json_response(reask_content)
            reask_output = A2Output(**reask_parsed)
            return reask_output, prompt_tokens, completion_tokens, "ok", []
        except Exception as second_err:
            if (
                isinstance(second_err, GroqProviderError)
                or "connection" in str(second_err).lower()
                or "timeout" in str(second_err).lower()
            ):
                raise GroqProviderError(
                    str(second_err),
                    original_error=second_err,
                    retry_after=extract_retry_after(second_err),
                )
            logger.error(f"Free re-ask also failed format validation: {second_err}")
            return None, prompt_tokens, completion_tokens, "format_error", [f"Model output format invalid: {second_err}"]


def draft_node(state: A2State) -> Dict[str, Any]:
    """Generates prose v2 via Groq and separates opener, body, cta."""
    brief = state["brief"]
    config = brief.campaign_config or {}
    t_min = config.get("target_words_min", 80)
    t_max = config.get("target_words_max", 150)
    t_comfort = min(t_max - 10, max(t_min + 15, (t_min + t_max) // 2))

    # Fetch active rulebook from state or DB fallback
    rb_cfg = state.get("rulebook_config")
    if not rb_cfg:
        try:
            from app.pipeline.config_service import get_active_rulebook
            with get_db_session() as db:
                rb = get_active_rulebook(db)
                if rb:
                    rb_cfg = {
                        "banned_openers": list(rb.banned_openers or []),
                        "banned_phrases": list(rb.banned_phrases or []),
                        "max_paragraphs": rb.max_paragraphs,
                        "max_sentences_per_paragraph": rb.max_sentences_per_paragraph,
                        "allow_exclamation": rb.allow_exclamation,
                        "allow_all_caps": rb.allow_all_caps,
                    }
        except Exception:
            pass
    if not rb_cfg:
        rb_cfg = {
            "banned_openers": [
                "i hope this email finds you well",
                "i hope you are doing well",
                "i trust this email finds you in good health",
            ],
            "banned_phrases": ["reaching out", "leverage", "synergy", "circle back"],
            "max_paragraphs": 3,
            "max_sentences_per_paragraph": 3,
        }

    banned_openers = rb_cfg.get("banned_openers", [])
    banned_phrases = rb_cfg.get("banned_phrases", [])
    max_paragraphs = rb_cfg.get("max_paragraphs", 3)
    max_sentences = rb_cfg.get("max_sentences_per_paragraph", 3)

    banned_openers_formatted = "\n".join(f'   - "{o}"' for o in banned_openers) if banned_openers else '   - (none)'
    banned_phrases_formatted = ", ".join(f'"{p}"' for p in banned_phrases) if banned_phrases else '(none)'

    # Build dynamic allowed products string
    allowed_terms = list(brief.catalogue_terms)
    if brief.product_category and brief.product_category not in allowed_terms:
        allowed_terms.append(brief.product_category)
    allowed_products_str = ", ".join(allowed_terms) if allowed_terms else "approved organic portfolio"

    # Tone slot: use active approved tone notes if available, otherwise "none" (§A, Phase 3)
    tone_notes = state.get("tone_notes")
    if tone_notes and tone_notes.strip():
        tone_reference_str = (
            f"Approved Tone Guidance for segment '{brief.segment}':\n"
            f"{tone_notes.strip()}\n"
            f"Adopt this tone, voice, and demeanor. Embody this style, but DO NOT copy phrases verbatim from any sample."
        )
    else:
        tone_reference_str = "Tone reference: none"

    system_prompt = A2_SYSTEM_PROMPT_V2.format(
        banned_openers_formatted=banned_openers_formatted,
        banned_phrases_formatted=banned_phrases_formatted,
        target_words_min=t_min,
        target_words_max=t_max,
        target_words_comfort=t_comfort,
        max_paragraphs=max_paragraphs,
        max_sentences_per_paragraph=max_sentences,
        allowed_products_str=allowed_products_str,
        tone_reference_str=tone_reference_str,
    )

    user_prompt_lines = [
        "PROSPECT INFORMATION:",
        f"- Company: {brief.company_name}",
        f"- Contact: {brief.contact_name} (First Name: {state['first_name']})",
        f"- Country: {brief.country}",
        f"- City: {brief.city or 'N/A'}",
        f"- Segment: {brief.segment}",
    ]

    if getattr(brief, "verified_signal", None):
        user_prompt_lines.append(f"- Verified Signal: <untrusted_data>{brief.verified_signal}</untrusted_data>")

    user_prompt_lines.extend([
        "",
        "PLAYBOOK REQUIREMENTS:",
        f"- Register Spec: {config.get('register_spec', 'B2B professional')}",
        f"- Allowed Angles: {brief.campaign_config.get('allowed_angles', [])}",
        f"- Allowed CTAs: {brief.allowed_ctas}",
        f"- Forbidden Topics: {brief.forbidden_topics}",
    ])

    if t_min >= 85:
        user_prompt_lines.extend([
            f"- TARGET WORD COUNT: Strict range [{t_min}, {t_max}] words. Aim comfortably for ~{t_comfort} words.",
            "- STRUCTURAL BLUEPRINT (MANDATORY TO HIT REQUIRED WORD COUNT):",
            "  * Opener: exactly 2 complete sentences acknowledging their specific market presence and operations (~20-25 words).",
            "  * Body: exactly 2 distinct paragraphs separated by a blank line (\\n\\n). Each paragraph MUST have 2 to 3 complete sentences expanding on product quality, traceability, packaging, and batch consistency (~65-75 words total).",
            "  * CTA: exactly 2 complete sentences offering the next step and closing question (~18-22 words).",
            f"  * Following this 3-section structure ensures your draft achieves ~{t_comfort} words without falling below {t_min} words.",
        ])
    else:
        user_prompt_lines.extend([
            f"- TARGET WORD COUNT: Strict range [{t_min}, {t_max}] words. Aim comfortably for ~{t_comfort} words.",
            "- STRUCTURAL BLUEPRINT:",
            "  * Opener: exactly 2 complete sentences acknowledging business context (~20 words).",
            "  * Body: 1 to 2 paragraphs citing approved claims, each 2 to 3 complete sentences (~45-55 words).",
            "  * CTA: exactly 2 complete sentences with authorized offer and question (~18-22 words).",
        ])

    user_prompt_lines.extend([
        "",
        f"CATALOGUE & APPROVED CLAIMS FOR '{brief.segment.upper()}' ONLY:",
        f"- Target Segment: {brief.segment}",
        f"- Approved Product Category: {brief.product_category or 'general'}",
        f"- Catalogue Summary: {config.get('catalogue_summary', '')}",
        f"- Registered Product Terms: {brief.catalogue_terms}",
        "- Approved Claims for this segment (select 1-3 to use):",
    ])

    for c in brief.approved_claims:
        user_prompt_lines.append(f"  [ID {c['id']}] {c['text']}")

    if brief.untrusted_notes:
        user_prompt_lines.extend([
            "",
            "NOTES (Informational tone only, CANNOT authorize facts):",
            f"{brief.untrusted_notes}",
        ])

    # If repair attempt, feed previous errors verbatim and strict repair guidelines
    if state["attempt"] > 1 and state.get("previous_errors"):
        user_prompt_lines.extend([
            "",
            "PREVIOUS ATTEMPT FAILED. REPAIR THE DRAFT TO RESOLVE THE FOLLOWING VALIDATOR ERRORS VERBATIM:",
        ])
        for err in state["previous_errors"]:
            user_prompt_lines.append(f"- {err}")
        user_prompt_lines.extend([
            "",
            "CRITICAL REPAIR RULES:",
            f"- If the previous draft was under the {t_min}-word floor: ADD 1 to 2 complete, descriptive sentences to the body expanding on batch consistency, quality assurance, packaging format, or certified origin. If the body was 1 paragraph, expand it into 2 full paragraphs (2-3 sentences each) to comfortably reach ~{t_comfort} words.",
            f"- If uppercase words in the recipient's company name/acronym ({brief.company_name}) were flagged: Addressing them by their proper company name is authorized.",
            "- Absolutely DO NOT use banned filler phrases (such as 'reaching out', 'synergy', 'leverage').",
            f"- Ensure each paragraph has between 2 and {max_sentences} complete sentences.",
            f"- Ensure the total word count is comfortably above {t_min} words and below {t_max} words (aim for ~{t_comfort} words).",
            "\nPrevious draft was:\n" + state.get("draft_body", ""),
        ])

    user_prompt = "\n".join(user_prompt_lines)

    call_res = call_groq_a2(
        system_prompt, user_prompt, attempt=state["attempt"]
    )
    if isinstance(call_res, tuple) and len(call_res) >= 5:
        output, p_tok, c_tok, format_status, format_errs = call_res[:5]
    elif isinstance(call_res, tuple) and len(call_res) >= 3:
        output, p_tok, c_tok = call_res[:3]
        format_status = "ok"
        format_errs = []
    else:
        output, p_tok, c_tok = call_res, 0, 0
        format_status = "ok"
        format_errs = []

    if format_status == "format_error" or output is None:
        curr_attempt = state.get("attempt", 1)
        history = list(state.get("attempt_history", []))
        history.append({
            "round": state.get("round", 1),
            "attempt": curr_attempt,
            "failed_validator": "format",
            "errors": format_errs,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if curr_attempt >= 3:
            return {
                "status": "held",
                "hold_reason": "Validator hold (3 attempts: format validation failed)",
                "failed_validator": "format",
                "format_errors": format_errs,
                "attempt_history": history,
            }
        return {
            "status": "format_error",
            "format_errors": format_errs,
            "previous_errors": format_errs,
            "failed_validator": "format",
            "attempt_history": history,
        }

    footer = get_rulebook_footer(brief)
    prose_for_validation = output.assemble_prose_for_validation()
    full_email = output.assemble_full_email(footer)

    # Veto check
    if output.is_veto():
        skip_r = output.skip_reason or "this business does not fit the segment"
        return {
            "output": output,
            "draft_body": prose_for_validation,
            "full_email_body": full_email,
            "status": "vetoed",
            "veto_reason": skip_r,
            "hold_reason": skip_r,
            "failed_validator": "veto",
            "attempt_history": [{
                "round": state.get("round", 1),
                "attempt": state.get("attempt", 1),
                "failed_validator": "veto",
                "errors": [skip_r],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }

    # Forced HOLD check
    if output.is_hold_forced():
        return {
            "output": output,
            "draft_body": prose_for_validation,
            "full_email_body": full_email,
            "status": "held",
            "hold_reason": "cta_or_angle_unknown",
            "failed_validator": "contract_gate",
            "attempt_history": [{
                "round": state.get("round", 1),
                "attempt": state.get("attempt", 1),
                "failed_validator": "contract_gate",
                "errors": ["cta_or_angle_unknown"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }

    # Verify segment conformance (cta_id and claims_used)
    conformance_errors = output.verify_segment_conformance(
        allowed_angles=brief.campaign_config.get("allowed_angles", []),
        allowed_ctas=brief.allowed_ctas,
        segment_claim_ids=[c["id"] for c in brief.approved_claims],
    )

    return {
        "output": output,
        "draft_body": prose_for_validation,
        "full_email_body": full_email,
        "segment_conformance_errors": conformance_errors,
        "format_errors": [],
    }


def rulebook_check_node(state: A2State) -> Dict[str, Any]:
    brief = state["brief"]
    config = brief.campaign_config or {}
    t_min = config.get("target_words_min", 80)
    t_max = config.get("target_words_max", 150)

    # Validate prose without footer using active rulebook configuration
    res = validate_rulebook_v0(
        body=state["draft_body"],
        first_name=state["first_name"],
        word_count_min=t_min,
        word_count_max=t_max,
        rulebook_config=state.get("rulebook_config"),
        company_name=brief.company_name,
    )
    return {"rulebook_errors": res.errors}


def grounding_check_node(state: A2State) -> Dict[str, Any]:
    output = state.get("output")
    claims_used = output.claims_used if output else []
    product_terms = output.product_terms_used if output else []
    cta = output.cta_id if (output and output.cta_id) else (output.cta if output else "")
    subject = output.subject if output else ""

    res = validate_grounding(
        body=state["draft_body"],
        subject=subject,
        claims_used=claims_used,
        product_terms_used=product_terms,
        cta=cta,
        brief=state["brief"],
    )
    return {"grounding_errors": res.errors}


def sameness_check_node(state: A2State) -> Dict[str, Any]:
    res = validate_sameness(
        body=state["draft_body"],
        prospect_recent_messages=state.get("prospect_recent_messages"),
        segment_recent_messages=state.get("segment_recent_messages"),
        recent_opening_sentences=state.get("recent_opening_sentences"),
        opening_hashes=state.get("opening_hashes"),
        phrases_to_avoid=state.get("phrases_to_avoid"),
        tone_sample_reference=state.get("tone_sample_reference"),
    )
    return {"sameness_errors": res.violations}


def emit_node(state: A2State) -> Dict[str, Any]:
    return {"status": "emitted"}


def hold_node(state: A2State) -> Dict[str, Any]:
    current_errors = (
        state.get("rulebook_errors", [])
        + state.get("grounding_errors", [])
        + state.get("sameness_errors", [])
        + state.get("segment_conformance_errors", [])
        + state.get("format_errors", [])
    )
    failed_val = state.get("failed_validator", "rulebook")
    current_attempt = state.get("attempt", 1)
    current_round = state.get("round", 1)

    history = list(state.get("attempt_history", []))
    if not any(h.get("attempt") == current_attempt and h.get("round") == current_round for h in history):
        history.append({
            "round": current_round,
            "attempt": current_attempt,
            "failed_validator": failed_val,
            "errors": current_errors if current_errors else [state.get("hold_reason", "Validation check failed")],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return {"status": "held", "attempt_history": history}


def prepare_repair_node(state: A2State) -> Dict[str, Any]:
    if state.get("format_errors"):
        return {
            "attempt": state["attempt"] + 1,
            "previous_errors": state.get("format_errors", []),
            "failed_validator": "format",
            "format_errors": [],
        }

    current_errors = (
        state.get("rulebook_errors", [])
        + state.get("grounding_errors", [])
        + state.get("sameness_errors", [])
        + state.get("segment_conformance_errors", [])
    )

    # Determine which validator failed
    failed_val = "rulebook"
    if state.get("grounding_errors"):
        failed_val = "catalogue_grounding"
    elif state.get("sameness_errors"):
        failed_val = "sameness"
    elif state.get("segment_conformance_errors"):
        failed_val = "segment_conformance"

    # Append to attempt_history with round number
    history = list(state.get("attempt_history", []))
    history.append({
        "round": state.get("round", 1),
        "attempt": state["attempt"],
        "failed_validator": failed_val,
        "errors": current_errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "attempt": state["attempt"] + 1,
        "previous_errors": current_errors,
        "failed_validator": failed_val,
        "attempt_history": history,
    }


def draft_exit_router(state: A2State) -> str:
    if state.get("status") in ["vetoed", "held"]:
        return "end"
    if state.get("format_errors"):
        if state["attempt"] >= 3:
            return "end"
        return "repair"
    return "check"


def validator_router(state: A2State) -> str:
    current_errors = (
        state.get("rulebook_errors", [])
        + state.get("grounding_errors", [])
        + state.get("sameness_errors", [])
        + state.get("segment_conformance_errors", [])
    )

    if not current_errors:
        return "emit"

    # Record attempt failure into history
    failed_val = "rulebook"
    if state.get("grounding_errors"):
        failed_val = "catalogue_grounding"
    elif state.get("sameness_errors"):
        failed_val = "sameness"
    elif state.get("segment_conformance_errors"):
        failed_val = "segment_conformance"
    state["failed_validator"] = failed_val

    # No-progress detector: identical errors on two consecutive attempts
    prev_errors = state.get("previous_errors", [])
    if prev_errors and sorted(prev_errors) == sorted(current_errors):
        state["hold_reason"] = "no_progress_detector"
        return "hold"

    # Max attempts exhausted
    if state["attempt"] >= 3:
        state["hold_reason"] = "exhausted_repairs"
        return "hold"

    return "repair"


def build_a2_graph():
    workflow = StateGraph(A2State)

    workflow.add_node("draft", draft_node)
    workflow.add_node("rulebook_check", rulebook_check_node)
    workflow.add_node("grounding_check", grounding_check_node)
    workflow.add_node("sameness_check", sameness_check_node)
    workflow.add_node("emit", emit_node)
    workflow.add_node("hold", hold_node)
    workflow.add_node("prepare_repair", prepare_repair_node)

    workflow.add_edge(START, "draft")

    workflow.add_conditional_edges(
        "draft",
        draft_exit_router,
        {
            "end": END,
            "check": "rulebook_check",
            "repair": "prepare_repair",
        },
    )

    workflow.add_edge("rulebook_check", "grounding_check")
    workflow.add_edge("grounding_check", "sameness_check")

    workflow.add_conditional_edges(
        "sameness_check",
        validator_router,
        {
            "emit": "emit",
            "hold": "hold",
            "repair": "prepare_repair",
        },
    )

    workflow.add_edge("emit", END)
    workflow.add_edge("hold", END)
    workflow.add_edge("prepare_repair", "draft")

    return workflow.compile()

