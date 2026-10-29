import re
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.db.models import Contact, Segment, Catalogue, ApprovedClaim, ToneReference
from app.pipeline.config_service import get_active_rulebook
from app.schemas.a2_contract import MessageMetadata
from app.validators.grounding import MessageBrief
from app.workers.a2_personalization.brief_assembler import assemble_brief
from app.workers.a2_personalization.graph import build_a2_graph, A2State


def extract_first_name(full_name: str) -> str:
    """Extracts first name from full contact name."""
    if not full_name:
        return "Partner"
    parts = re.split(r"[\s,]+", full_name.strip())
    # Handle prefixes like Mr., Ms., Dr.
    if parts[0].lower() in ["mr.", "mr", "ms.", "ms", "dr.", "dr", "mrs.", "mrs"]:
        return parts[1] if len(parts) > 1 else parts[0]
    return parts[0]


def run_a2_for_contact(
    contact_id: int,
    db: Session,
    campaign_config: Optional[Dict[str, Any]] = None,
    thread_digest: Optional[Dict[str, Any]] = None,
    last_message: Optional[str] = None,
    include_notes: bool = False,
    include_pii: bool = False,
    prospect_recent_messages: Optional[List[str]] = None,
    segment_recent_messages: Optional[List[str]] = None,
    recent_opening_sentences: Optional[List[str]] = None,
    opening_hashes: Optional[List[str]] = None,
    phrases_to_avoid: Optional[List[str]] = None,
    round_number: int = 1,
    existing_history: Optional[List[Dict[str, Any]]] = None,
) -> A2State:
    """
    Main orchestration service for running A2 PersonalizationAgent on a contact record.
    """
    # 1. Fetch Contact
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise ValueError(f"Contact ID {contact_id} not found in database.")

    # 2. Fetch Segment
    segment = db.query(Segment).filter(Segment.segment == contact.segment).first()
    if not segment:
        raise ValueError(f"Segment '{contact.segment}' not found for contact ID {contact_id}.")

    # 3. Fetch Catalogue
    catalogue = db.query(Catalogue).filter(Catalogue.id == segment.catalogue_id).first()
    if not catalogue:
        raise ValueError(f"Catalogue ID {segment.catalogue_id} not found for segment '{segment.segment}'.")

    # 4. Fetch Approved Claims for this segment (active only §Phase 9)
    approved_claims = (
        db.query(ApprovedClaim)
        .filter(ApprovedClaim.segment == segment.segment, ApprovedClaim.is_active == True)
        .all()
    )

    # 5. Fetch other catalogues' terms for cross-catalogue check (check h2)
    other_catalogues = db.query(Catalogue).filter(Catalogue.id != catalogue.id).all()
    other_terms = set()
    other_terms_map = {}
    for cat in other_catalogues:
        cat_segs = [s.segment for s in cat.segments] if cat.segments else [cat.name]
        seg_label = ", ".join(cat_segs) if cat_segs else cat.name
        for t in cat.registered_term_list or []:
            other_terms.add(t)
            other_terms_map[t] = seg_label

    product_category = approved_claims[0].product_category if approved_claims else "general"

    # 6. Assemble Brief
    brief = assemble_brief(
        contact=contact,
        segment=segment,
        catalogue=catalogue,
        approved_claims=approved_claims,
        other_catalogues_terms=list(other_terms),
        thread_digest=thread_digest,
        last_message=last_message,
        campaign_config=campaign_config,
        include_notes=include_notes,
        include_pii=include_pii,
        product_category=product_category,
        other_catalogues_term_map=other_terms_map,
    )

    first_name = extract_first_name(contact.contact_name)

    # 7. Fetch active approved Tone Reference for this segment (§A, Phase 3)
    active_tone = (
        db.query(ToneReference)
        .filter(
            ToneReference.segment == segment.segment,
            ToneReference.is_active == True,
            ToneReference.status == "approved",
        )
        .first()
    )
    tone_notes = active_tone.tone_notes if active_tone else None
    tone_sample_reference = active_tone.draft_body if active_tone else None

    # 8. Fetch active rulebook configuration from DB
    active_rb = get_active_rulebook(db)
    rb_dict = {
        "banned_openers": list(active_rb.banned_openers or []),
        "banned_phrases": list(active_rb.banned_phrases or []),
        "max_paragraphs": active_rb.max_paragraphs,
        "max_sentences_per_paragraph": active_rb.max_sentences_per_paragraph,
        "allow_exclamation": active_rb.allow_exclamation,
        "allow_all_caps": active_rb.allow_all_caps,
    } if active_rb else None

    # 9. Initialize Graph State
    initial_state: A2State = {
        "brief": brief,
        "first_name": first_name,
        "round": round_number,
        "attempt": 1,
        "draft_body": "",
        "full_email_body": "",
        "output": None,
        "metadata": None,
        "rulebook_errors": [],
        "grounding_errors": [],
        "sameness_errors": [],
        "segment_conformance_errors": [],
        "format_errors": [],
        "previous_errors": [],
        "status": "pending",
        "hold_reason": None,
        "failed_validator": None,
        "veto_reason": None,
        "attempt_history": list(existing_history or []),
        "prospect_recent_messages": prospect_recent_messages or [],
        "segment_recent_messages": segment_recent_messages or [],
        "recent_opening_sentences": recent_opening_sentences or [],
        "opening_hashes": opening_hashes or [],
        "phrases_to_avoid": phrases_to_avoid or [],
        "tone_notes": tone_notes,
        "tone_sample_reference": tone_sample_reference,
        "rulebook_config": rb_dict,
    }

    # 10. Execute Graph
    graph = build_a2_graph()
    final_state = graph.invoke(initial_state)

    # Backward compatibility: populate metadata from output if needed
    if final_state.get("output") and not final_state.get("metadata"):
        out = final_state["output"]
        final_state["metadata"] = MessageMetadata(
            subject=out.subject,
            angle_used=out.angle,
            cta=out.cta,
            claims_used=out.claims_used,
            product_terms_used=out.product_terms_used,
            confidence=out.confidence,
            skip_reason=out.skip_reason,
        )

    return final_state
