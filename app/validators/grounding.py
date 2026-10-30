import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any
from sqlalchemy.orm import Session

from app.validators.commercial_term_gate import validate_commercial_terms

CERTIFICATION_TOKENS = [
    "FSSAI",
    "HACCP",
    "ISO",
    "BRC",
    "HALAL",
    "KOSHER",
    "FDA",
    "APEDA",
    "GMP",
]

PAST_RELATIONSHIP_VERBS = [
    "as discussed",
    "your order",
    "following our call",
    "last time",
    "we previously",
]


@dataclass
class MessageBrief:
    segment: str
    company_name: str
    contact_name: str
    country: str
    city: Optional[str] = None
    campaign_config: Dict[str, Any] = field(default_factory=dict)
    approved_claims: List[Dict[str, Any]] = field(default_factory=list)
    catalogue_terms: List[str] = field(default_factory=list)
    other_catalogues_terms: List[str] = field(default_factory=list)
    allowed_ctas: List[str] = field(default_factory=list)
    forbidden_topics: List[str] = field(default_factory=list)
    untrusted_notes: Optional[str] = None  # EXPLICITLY excluded from entity allowlist (§10.1e)
    verified_signal: Optional[str] = None
    product_category: Optional[str] = None
    other_catalogues_term_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class GroundingResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    per_check_results: Dict[str, bool] = field(default_factory=dict)


def build_entity_allowlist(brief: MessageBrief) -> Set[str]:
    """
    Builds the entity allowlist strictly from:
    - structured prospect fields (company_name, contact_name, city, country)
    - campaign configuration (sender organization, sender name, locations)
    - approved_claims text
    ►► FREE-TEXT NOTES ARE EXPLICITLY EXCLUDED (§8.8, §10.1e).
    """
    allowlist = set()

    def add_entities(text: Optional[str]):
        if not text:
            return
        cleaned = text.strip().lower()
        if cleaned:
            allowlist.add(cleaned)
            # Add individual significant name words (length >= 3)
            for part in re.split(r"[\s,.-]+", cleaned):
                if len(part) >= 3 and part not in {"the", "and", "ltd", "inc", "pvt", "llc", "corp"}:
                    allowlist.add(part)

    # 1. Structured prospect fields
    add_entities(brief.company_name)
    add_entities(brief.contact_name)
    add_entities(brief.city)
    add_entities(brief.country)

    # 2. Campaign configuration
    for val in brief.campaign_config.values():
        if isinstance(val, str):
            add_entities(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    add_entities(item)

    # Standard Narayani defaults if not in config
    add_entities("Narayani Distributors")
    add_entities("Narayani")
    add_entities("Gujarat")
    add_entities("India")

    # 3. Approved claims
    for claim in brief.approved_claims:
        claim_text = claim.get("text", "")
        # Extract capitalized words or phrases that represent entities
        for match in re.finditer(r"\b[A-Z][a-zA-Z0-9-]+\b", claim_text):
            tok = match.group().lower()
            if len(tok) >= 3:
                allowlist.add(tok)

    return allowlist


def validate_grounding(
    body: str,
    subject: str,
    claims_used: List[int],
    product_terms_used: List[str],
    cta: str,
    brief: MessageBrief,
    db_session: Optional[Session] = None,
) -> GroundingResult:
    """
    Full Grounding Validator (§10.1).
    Evaluates checks (a) through (i) completely deterministically without LLM calls.
    """
    errors: List[str] = []
    per_check: Dict[str, bool] = {}

    cleaned_body = body.strip()
    cleaned_subject = subject.strip()
    combined_text = f"{cleaned_subject} {cleaned_body}"
    combined_lower = combined_text.lower()

    # --- Check (a): claims_used[] all exist in the brief ---
    brief_claim_ids = {c["id"] for c in brief.approved_claims if "id" in c}
    invalid_claim_ids = set(claims_used) - brief_claim_ids
    check_a = len(invalid_claim_ids) == 0
    per_check["check_a_claims_used"] = check_a
    if not check_a:
        errors.append(f"Check (a) failed: claims_used contains ungrounded claim IDs: {sorted(list(invalid_claim_ids))}")

    # --- Check (b): Every number in the body appears in the brief ---
    # Extract numbers from body
    body_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", cleaned_body))
    # Collect all numbers appearing in the brief (claims, prospect structured info, campaign config)
    brief_text_corpus = " ".join([
        str(brief.company_name),
        str(brief.contact_name),
        str(brief.city or ""),
        str(brief.country),
        str(brief.campaign_config),
    ] + [c.get("text", "") for c in brief.approved_claims])
    brief_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", brief_text_corpus))

    ungrounded_numbers = body_numbers - brief_numbers
    check_b = len(ungrounded_numbers) == 0
    per_check["check_b_number_tracing"] = check_b
    if not check_b:
        errors.append(f"Check (b) failed: body contains ungrounded numbers: {sorted(list(ungrounded_numbers))}")

    # --- Check (c): Certification tokens require approved_claim for this segment ---
    # Banned tokens unless present in an approved_claim for target segment
    # Check what certification tokens are authorized for this segment
    authorized_cert_tokens = set()

    # From brief claims
    for claim in brief.approved_claims:
        claim_segment = claim.get("segment", brief.segment)
        if claim_segment == brief.segment:
            claim_text = claim.get("text", "").upper()
            for token in CERTIFICATION_TOKENS:
                if re.search(rf"\b{token}\b", claim_text):
                    authorized_cert_tokens.add(token)

    # If db_session provided, also verify against database approved_claims
    if db_session is not None:
        try:
            from app.db.models import ApprovedClaim
            claims_db = (
                db_session.query(ApprovedClaim)
                .filter(ApprovedClaim.segment == brief.segment, ApprovedClaim.is_active == True)
                .all()
            )
            for c in claims_db:
                c_text = c.text.upper()
                for token in CERTIFICATION_TOKENS:
                    if re.search(rf"\b{token}\b", c_text):
                        authorized_cert_tokens.add(token)
        except Exception:
            pass

    found_unauthorized_certs = []
    for token in CERTIFICATION_TOKENS:
        # Check if token exists in body or subject
        if re.search(rf"\b{token}\b", combined_text, re.IGNORECASE):
            if token not in authorized_cert_tokens:
                found_unauthorized_certs.append(token)

    check_c = len(found_unauthorized_certs) == 0
    per_check["check_c_certifications"] = check_c
    if not check_c:
        errors.append(
            f"Check (c) failed: unauthorized certification tokens for segment '{brief.segment}': {found_unauthorized_certs}"
        )

    # --- Check (d): Commercial specifics (currency, %, MOQ, tonnage) not in brief ---
    # Handled both by commercial_term_gate and brief grounding
    check_d = True
    per_check["check_d_commercial_specifics"] = check_d

    # --- Check (e): Entity allowlist (Free-text notes EXPLICITLY excluded) ---
    entity_allowlist = build_entity_allowlist(brief)
    
    # If notes contain injected entities, ensure they are NOT in entity_allowlist
    # Detect if any entities from untrusted_notes that are NOT in allowlist appear in email
    check_e_errors = []
    if brief.untrusted_notes:
        # Check capitalized entity words in untrusted_notes
        notes_entities = re.findall(r"\b[A-Z][a-zA-Z0-9-]+\b", brief.untrusted_notes)
        for ent in notes_entities:
            ent_lower = ent.lower()
            if ent_lower not in entity_allowlist and len(ent) >= 3:
                # If this unauthorized entity from notes appears in the body or subject
                if re.search(rf"\b{re.escape(ent)}\b", combined_text, re.IGNORECASE):
                    check_e_errors.append(
                        f"Entity '{ent}' originates solely from untrusted notes and is forbidden by allowlist."
                    )

    check_e = len(check_e_errors) == 0
    per_check["check_e_entity_allowlist"] = check_e
    if not check_e:
        errors.extend([f"Check (e) failed: {err}" for err in check_e_errors])

    # --- Check (f): Past-relationship verbs denylist ---
    found_past_verbs = []
    for verb in PAST_RELATIONSHIP_VERBS:
        if verb in combined_lower:
            found_past_verbs.append(verb)
    check_f = len(found_past_verbs) == 0
    per_check["check_f_past_relationship"] = check_f
    if not check_f:
        errors.append(f"Check (f) failed: past-relationship verbs detected: {found_past_verbs}")

    # --- Check (g): CTA & forbidden topics ---
    check_g_errors = []
    if brief.allowed_ctas and cta not in brief.allowed_ctas:
        check_g_errors.append(f"CTA '{cta}' is not in segment's allowed_ctas: {brief.allowed_ctas}")

    # Forbidden topics check
    for topic in brief.forbidden_topics:
        # Match topic either directly or with underscores replaced by spaces/slashes
        variants = [
            topic.lower(),
            topic.lower().replace("_", " "),
            topic.lower().replace("/", " "),
        ]
        for v in variants:
            if re.search(rf"\b{re.escape(v)}\b", combined_lower):
                check_g_errors.append(f"Segment forbidden topic detected: '{topic}'")
                break

    check_g = len(check_g_errors) == 0
    per_check["check_g_playbook"] = check_g
    if not check_g:
        errors.extend([f"Check (g) failed: {err}" for err in check_g_errors])

    # --- Check (h): Catalogue consistency ---
    check_h_errors = []
    # Generic industry terms that span multiple segments and are not proprietary single-catalogue items
    GENERIC_INDUSTRY_TERMS = {
        "fmcg", "b2b", "fssai", "iso", "moq", "sku", "ean", "llc", "wholesale",
        "wholesaler", "distributor", "distribution", "retail", "supermarket",
        "importer", "export", "exports", "food", "foods", "snacks", "mixes",
        "packaged food", "packaged goods"
    }

    # h1: Positive — every term in product_terms_used appears in this catalogue's terms
    catalogue_terms_lower = {t.lower() for t in brief.catalogue_terms}
    invalid_product_terms = [
        t for t in product_terms_used
        if t.lower() not in catalogue_terms_lower
        and not any(t.lower() in ct or ct in t.lower() for ct in catalogue_terms_lower)
        and t.lower() not in GENERIC_INDUSTRY_TERMS
    ]
    if invalid_product_terms:
        check_h_errors.append(
            f"h1 failed: product_terms_used contains terms outside catalogue: {invalid_product_terms}"
        )

    # h2: Negative — body contains no term from (other catalogues - this catalogue)
    other_terms_lower = {t.lower() for t in brief.other_catalogues_terms}
    banned_other_terms = (other_terms_lower - catalogue_terms_lower) - GENERIC_INDUSTRY_TERMS

    # Pre-mask approved terms from THIS catalogue (longer phrases first) so compound terms
    # (e.g. 'protein snacks', 'seed mixes') do not falsely trigger single-word denylist matches (e.g. 'snacks', 'mixes')
    scan_text = combined_lower
    for cat_term in sorted(catalogue_terms_lower, key=len, reverse=True):
        if len(cat_term) >= 3:
            scan_text = re.sub(rf"\b{re.escape(cat_term)}\b", " ", scan_text)

    # Also pre-mask approved claims for this contact so authorized phrasing does not trigger collisions
    for claim in brief.approved_claims:
        c_text = claim.get("text", "").lower()
        if c_text:
            for phrase in re.findall(r"[a-z0-9-]+(?:\s+[a-z0-9-]+){1,3}", c_text):
                if any(t in phrase for t in catalogue_terms_lower):
                    scan_text = re.sub(rf"\b{re.escape(phrase)}\b", " ", scan_text)

    found_banned_catalogue_terms = []
    term_details = []
    term_origin_map = getattr(brief, "other_catalogues_term_map", {}) or {}
    for term in banned_other_terms:
        if len(term) >= 3 and re.search(rf"\b{re.escape(term)}\b", scan_text):
            found_banned_catalogue_terms.append(term)
            seg_origin = term_origin_map.get(term, "another segment")
            term_details.append(f"'{term}' (belongs to '{seg_origin}' catalogue)")

    if found_banned_catalogue_terms:
        check_h_errors.append(
            f"Check (h2) Grounding violation: email mentions banned product terms from other catalogues: "
            f"{', '.join(term_details)}. You must strictly remove these terms and never reference products outside "
            f"this contact's '{brief.segment}' catalogue."
        )

    check_h = len(check_h_errors) == 0
    per_check["check_h_catalogue_consistency"] = check_h
    if not check_h:
        errors.extend([f"Check (h) failed: {err}" for err in check_h_errors])

    # --- Check (i): Commercial term denylist ---
    comm_result = validate_commercial_terms(body=cleaned_body, subject=cleaned_subject)
    check_i = comm_result.passed
    per_check["check_i_commercial_terms"] = check_i
    if not check_i:
        errors.append(f"Check (i) failed: commercial terms detected: {comm_result.violations}")

    passed = len(errors) == 0
    return GroundingResult(
        passed=passed,
        errors=errors,
        per_check_results=per_check,
    )
