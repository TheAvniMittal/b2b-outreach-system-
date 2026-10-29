import csv
import io
import datetime
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.db.models import ApprovedClaim, RulebookConfig, Segment, AuditReason, record_audit_log
from app.validators.commercial_term_gate import validate_commercial_terms

logger = logging.getLogger("config_service")


# ---------------------------------------------------------------------------
# 1. Approved Claims Management
# ---------------------------------------------------------------------------

def create_approved_claim(
    session: Session,
    segment: str,
    product_category: str,
    claim_type: str,
    text: str,
    evidence_note: Optional[str] = None,
    valid_from: Optional[datetime.date] = None,
    valid_to: Optional[datetime.date] = None,
    operator_name: str = "operator",
) -> ApprovedClaim:
    """
    Creates an approved claim after running commercial-term gate validation (VISION.md §10.8).
    Rejects claims containing prices, MOQs, payment terms.
    """
    # 1. Validate commercial-term gate
    gate_result = validate_commercial_terms(text)
    if not gate_result.passed:
        violations_str = "; ".join(gate_result.violations)
        raise ValueError(f"Claim rejected: contains unapproved commercial terms ({violations_str}).")

    # 2. Check segment exists
    seg_obj = session.query(Segment).filter(Segment.segment == segment).first()
    if not seg_obj:
        raise ValueError(f"Segment '{segment}' does not exist.")

    # 3. Create claim
    today = datetime.date.today()
    claim = ApprovedClaim(
        segment=segment,
        product_category=product_category.strip(),
        claim_type=claim_type.strip(),
        text=text.strip(),
        evidence_note=evidence_note.strip() if evidence_note else None,
        is_active=True,
        valid_from=valid_from or today,
        valid_to=valid_to,
        last_reviewed_at=today,
    )
    session.add(claim)
    session.flush()

    # 4. Audit
    record_audit_log(
        session=session,
        actor=operator_name or "operator",
        action="create_approved_claim",
        object_type="approved_claim",
        object_id=str(claim.id),
        reason=AuditReason.CLAIMS_UPLOADED,
        prev_state="none",
        new_state="active",
    )
    session.commit()
    return claim


def upload_claims_csv(
    session: Session,
    csv_content: str,
    operator_name: str = "operator",
) -> Dict[str, Any]:
    """
    Uploads approved claims from CSV content.
    Expected CSV columns: segment, product_category, claim_type, text, evidence_note (optional)
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    created = []
    errors = []

    row_num = 1
    for row in reader:
        row_num += 1
        segment = (row.get("segment") or "").strip()
        cat = (row.get("product_category") or "").strip()
        c_type = (row.get("claim_type") or "").strip()
        text = (row.get("text") or "").strip()
        evidence = (row.get("evidence_note") or "").strip() or None

        if not segment or not text:
            errors.append(f"Row {row_num}: Missing required 'segment' or 'text'")
            continue

        try:
            claim = create_approved_claim(
                session=session,
                segment=segment,
                product_category=cat or "general",
                claim_type=c_type or "standard",
                text=text,
                evidence_note=evidence,
                operator_name=operator_name,
            )
            created.append(claim.id)
        except Exception as e:
            errors.append(f"Row {row_num}: {str(e)}")

    return {
        "created_count": len(created),
        "created_ids": created,
        "errors": errors,
    }


def retire_claim(
    session: Session,
    claim_id: int,
    operator_name: str = "operator",
) -> bool:
    """
    Retires an approved claim (soft delete / is_active = False).
    Claims are retired, never permanently deleted (VISION.md §14.8).
    """
    claim = session.query(ApprovedClaim).filter(ApprovedClaim.id == claim_id).first()
    if not claim:
        return False

    prev_state = "active" if claim.is_active else "retired"
    claim.is_active = False

    record_audit_log(
        session=session,
        actor=operator_name or "operator",
        action="retire_approved_claim",
        object_type="approved_claim",
        object_id=str(claim.id),
        reason=AuditReason.ADMIN_PAUSE,
        prev_state=prev_state,
        new_state="retired",
    )
    session.commit()
    return True


# ---------------------------------------------------------------------------
# 2. Rulebook Configuration Management
# ---------------------------------------------------------------------------

def get_active_rulebook(session: Session) -> RulebookConfig:
    """
    Returns the currently active RulebookConfig from DB.
    If none exists, initializes version 1.
    """
    rb = (
        session.query(RulebookConfig)
        .filter(RulebookConfig.is_active == True)
        .order_by(RulebookConfig.version.desc())
        .first()
    )
    if rb:
        return rb

    # Initialize default if table empty
    default_rb = RulebookConfig(
        version=1,
        name="Default House Rulebook v1",
        is_active=True,
        banned_openers=[
            "i hope this email finds you well",
            "i hope you are doing well",
            "i trust this email finds you in good health",
        ],
        banned_phrases=[
            "reaching out",
            "leverage",
            "synergy",
            "circle back",
        ],
        max_paragraphs=3,
        max_sentences_per_paragraph=3,
        allow_exclamation=False,
        allow_all_caps=False,
        created_by="system",
    )
    session.add(default_rb)
    session.commit()
    return default_rb


def update_rulebook(
    session: Session,
    name: str,
    banned_openers: List[str],
    banned_phrases: List[str],
    max_paragraphs: int = 3,
    max_sentences_per_paragraph: int = 3,
    allow_exclamation: bool = False,
    allow_all_caps: bool = False,
    operator_name: str = "operator",
) -> RulebookConfig:
    """
    Creates a new active version of the rulebook and retires the old one.
    Records audit row with new version.
    """
    # Deactivate previous active rulebooks
    prev = session.query(RulebookConfig).filter(RulebookConfig.is_active == True).first()
    next_ver = 1
    if prev:
        next_ver = prev.version + 1
        prev.is_active = False

    new_rb = RulebookConfig(
        version=next_ver,
        name=name.strip() or f"Rulebook v{next_ver}",
        is_active=True,
        banned_openers=[o.strip().lower() for o in banned_openers if o.strip()],
        banned_phrases=[p.strip().lower() for p in banned_phrases if p.strip()],
        max_paragraphs=max_paragraphs,
        max_sentences_per_paragraph=max_sentences_per_paragraph,
        allow_exclamation=allow_exclamation,
        allow_all_caps=allow_all_caps,
        created_by=operator_name or "operator",
    )
    session.add(new_rb)
    session.flush()

    record_audit_log(
        session=session,
        actor=operator_name or "operator",
        action="update_rulebook",
        object_type="rulebook",
        object_id=str(new_rb.id),
        reason=AuditReason.RULEBOOK_UPDATED,
        prev_state=f"v{prev.version}" if prev else "none",
        new_state=f"v{new_rb.version}",
    )
    session.commit()
    return new_rb
