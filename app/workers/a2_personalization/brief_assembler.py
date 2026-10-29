import re
from typing import List, Dict, Optional, Any, Union
from app.validators.grounding import MessageBrief

# Hard token budget ceiling (§8.6, §9.2)
A2_INPUT_CEILING = 4100


def estimate_tokens(text: str) -> int:
    """
    Standard token estimator: roughly 1 token per 3.5 characters or 1.3 tokens per word.
    Conservative upper bound for safety.
    """
    if not text:
        return 0
    words = len(re.findall(r"\S+", text))
    chars = len(text)
    return max(int(words * 1.35), int(chars / 3.5))


def truncate_to_tokens(text: Optional[str], max_tokens: int) -> str:
    """Truncates text to stay within max_tokens budget."""
    if not text:
        return ""
    words = text.strip().split()
    # Approx 1.3 tokens per word -> max_words ≈ max_tokens / 1.3
    max_words = int(max_tokens / 1.3)
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        val = obj.get(key, default)
        return val if val is not None else default
    val = getattr(obj, key, default)
    return val if val is not None else default


def assemble_brief(
    contact: Any,
    segment: Any,
    catalogue: Any,
    approved_claims: List[Any],
    other_catalogues_terms: Optional[List[str]] = None,
    thread_digest: Optional[Dict[str, Any]] = None,
    last_message: Optional[str] = None,
    campaign_config: Optional[Dict[str, Any]] = None,
    include_notes: bool = False,
    include_pii: bool = False,
    product_category: Optional[str] = None,
    other_catalogues_term_map: Optional[Dict[str, str]] = None,
) -> MessageBrief:
    """
    ContextAssembler for A2 PersonalizationAgent (§8, §9.2, §10.8, §14.7).

    Pulls:
    - segment playbook (allowed_angles, allowed_ctas, forbidden_topics, register_spec, target_words)
    - catalogue summary (<= 200 tokens)
    - up to 5 segment-scoped approved_claims (FILTER OUT claim_type == 'commercial' per §10.8/§10.7)
    - thread digest (<= 300 tokens)
    - last message verbatim (<= 400 tokens)

    Enforces:
    - A2_INPUT_CEILING = 4100 as a hard assertion
    - PII minimisation per §14.7 (omits email/phone/notes unless explicitly opted in)
    - Imported free-text notes, if opted in, fenced as <untrusted_data> and excluded from entity allowlist (§8.8)
    """
    # 1. Contact fields (PII minimisation §14.7)
    company_name = _get(contact, "company_name", "")
    contact_name = _get(contact, "contact_name", "")
    country = _get(contact, "country", "India")
    city = _get(contact, "city", None)
    raw_notes = _get(contact, "notes", None)
    verified_signal = _get(contact, "verified_signal", None)

    # 2. Free-text notes handling (§8.8, §14.7)
    untrusted_notes = None
    if include_notes and raw_notes:
        cleaned_notes = str(raw_notes).strip()
        if cleaned_notes:
            untrusted_notes = f"<untrusted_data>{cleaned_notes}</untrusted_data>"

    # 3. Segment Playbook
    segment_name = _get(segment, "segment", "")
    allowed_angles = _get(segment, "allowed_angles", [])
    allowed_ctas = _get(segment, "allowed_ctas", [])
    forbidden_topics = _get(segment, "forbidden_topics", [])
    register_spec = _get(segment, "register_spec", "")
    target_words_min = _get(segment, "target_words_min", 80)
    target_words_max = _get(segment, "target_words_max", 120)

    # 4. Catalogue Summary (<= 200 tokens) & Registered Terms
    cat_summary_raw = _get(catalogue, "summary", "")
    cat_summary = truncate_to_tokens(cat_summary_raw, max_tokens=200)
    cat_terms = _get(catalogue, "registered_term_list", [])

    # 5. Approved claims filtering (§10.8, §10.7)
    # FILTER OUT any claim with claim_type == "commercial" before it ever reaches this brief!
    # Pick up to 5 segment-scoped claims
    eligible_claims = []
    for claim in approved_claims:
        c_type = _get(claim, "claim_type", "")
        c_segment = _get(claim, "segment", "")
        
        # Strictly exclude commercial claims
        if str(c_type).lower() == "commercial":
            continue
        
        # Ensure segment matches if specified
        if c_segment and c_segment != segment_name:
            continue

        c_id = _get(claim, "id", None)
        c_text = _get(claim, "text", "")
        c_prod_cat = _get(claim, "product_category", "")
        if not product_category and c_prod_cat:
            product_category = c_prod_cat
        eligible_claims.append({
            "id": c_id,
            "segment": c_segment,
            "product_category": c_prod_cat,
            "claim_type": c_type,
            "text": c_text,
        })
        if len(eligible_claims) >= 5:
            break

    # 6. Thread digest (<= 300 tokens) & Last message verbatim (<= 400 tokens)
    thread_digest_str = ""
    if thread_digest:
        digest_parts = []
        for k, v in thread_digest.items():
            digest_parts.append(f"{k}: {v}")
        thread_digest_str = truncate_to_tokens("\n".join(digest_parts), max_tokens=300)

    truncated_last_message = truncate_to_tokens(last_message, max_tokens=400) if last_message else None

    # 7. Campaign config
    merged_campaign_config = dict(campaign_config or {})
    merged_campaign_config.update({
        "target_words_min": target_words_min,
        "target_words_max": target_words_max,
        "register_spec": register_spec,
        "allowed_angles": allowed_angles,
        "catalogue_summary": cat_summary,
        "product_category": product_category or "general",
    })
    if thread_digest_str:
        merged_campaign_config["thread_digest"] = thread_digest_str
    if truncated_last_message:
        merged_campaign_config["last_message"] = truncated_last_message

    # Include PII only if explicitly opted in (§14.7)
    if include_pii:
        email = _get(contact, "email", None)
        if email:
            merged_campaign_config["prospect_email"] = email

    brief = MessageBrief(
        segment=segment_name,
        company_name=company_name,
        contact_name=contact_name,
        country=country,
        city=city,
        campaign_config=merged_campaign_config,
        approved_claims=eligible_claims,
        catalogue_terms=list(cat_terms),
        other_catalogues_terms=list(other_catalogues_terms or []),
        allowed_ctas=list(allowed_ctas),
        forbidden_topics=list(forbidden_topics),
        untrusted_notes=untrusted_notes,
        verified_signal=verified_signal,
        product_category=product_category or "general",
        other_catalogues_term_map=dict(other_catalogues_term_map or {}),
    )

    # 8. Hard Token Ceiling Assertion (§8.6, §9.2)
    # Estimate total tokens for prompt
    full_prompt_text = " ".join([
        company_name,
        contact_name,
        country,
        city or "",
        register_spec,
        cat_summary,
        thread_digest_str,
        truncated_last_message or "",
        untrusted_notes or "",
    ] + [c["text"] for c in eligible_claims] + cat_terms)

    estimated = estimate_tokens(full_prompt_text)
    if estimated > A2_INPUT_CEILING:
        raise ValueError(
            f"A2_INPUT_CEILING exceeded: estimated {estimated} tokens exceeds ceiling of {A2_INPUT_CEILING} tokens."
        )

    return brief
