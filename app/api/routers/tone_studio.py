"""
Tone Studio API Router (§A).
Provides endpoints for segment-specific tone exploration, sample generation,
validator checklist, approval, rejection, rollback, and writer preview.
"""

from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.db.models import Segment
from app.workers.tone_studio.service import (
    get_daily_studio_usage,
    get_or_create_session,
    get_active_tone_reference,
    get_tone_version_history,
    get_tone_rejections,
    generate_tone_sample,
    approve_tone_reference,
    reject_tone_sample,
    rollback_tone_reference,
    get_writer_preview_payload,
)
from app.workers.tone_studio.synthetic_contacts import get_synthetic_contact

router = APIRouter(prefix="/api/tone-studio", tags=["Tone Studio"])

DEFAULT_QUICK_PROMPTS = [
    {"label": "Warmer", "prompt": "Make the tone warmer and more approachable, emphasizing genuine collaborative partnership."},
    {"label": "More formal", "prompt": "Make the tone more formal, dignified, and executive-level for corporate leadership."},
    {"label": "More direct", "prompt": "Make the tone crisp and direct, getting straight to the commercial proposition with zero fluff."},
    {"label": "Less salesy", "prompt": "Make the tone less salesy and commercial, sounding like a peer industry inquiry rather than a pitch."},
    {"label": "Simpler words", "prompt": "Use simpler, plainer vocabulary and shorter sentences, making the message effortless to skim."},
]


def get_db():
    with get_db_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class GenerateSampleRequest(BaseModel):
    instruction: str = Field(..., min_length=2, description="Tone prompt or quick button choice")
    operator_name: str = Field(..., min_length=1, description="Operator name performing the action")


class ApproveDraftRequest(BaseModel):
    draft_body: str = Field(..., min_length=10, description="Full draft body being approved")
    tone_notes: str = Field(..., min_length=3, description="Tone summary notes")
    operator_name: str = Field(..., min_length=1, description="Operator name approving the tone")


class RejectDraftRequest(BaseModel):
    draft_body: str = Field(..., min_length=10, description="Draft body being rejected")
    reason: Optional[str] = Field(None, description="Optional rejection reason")
    operator_name: str = Field("operator", description="Operator name")


class RollbackRequest(BaseModel):
    operator_name: str = Field(..., min_length=1, description="Operator name initiating rollback")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/segments")
def list_segments(session: Session = Depends(get_db)):
    """Lists all segments with active tone reference status and daily token usage."""
    segments = session.query(Segment).order_by(Segment.segment).all()
    out = []
    for s in segments:
        usage = get_daily_studio_usage(session, s.segment)
        active_ref = get_active_tone_reference(session, s.segment)
        out.append({
            "segment": s.segment,
            "name": s.name,
            "target_words": f"{s.target_words_min}–{s.target_words_max}",
            "active_version": active_ref.version_number if active_ref else None,
            "active_tone_notes": active_ref.tone_notes if active_ref else "Default neutral",
            "approved_at": active_ref.created_at.isoformat() if active_ref and active_ref.created_at else None,
            "used_today": usage["used_today"],
            "remaining_today": usage["remaining_today"],
            "limit": usage["limit"],
            "resets_at": usage["resets_at"],
        })
    return {"segments": out}


@router.get("/{segment}/chat")
def get_segment_chat(segment: str, session: Session = Depends(get_db)):
    """Retrieves segment chat history, active tone, version history, rejections, and synthetic contact."""
    seg_obj = session.query(Segment).filter(Segment.segment == segment).first()
    if not seg_obj:
        raise HTTPException(status_code=404, detail=f"Segment '{segment}' not found.")

    studio_sess = get_or_create_session(session, segment)
    usage = get_daily_studio_usage(session, segment)
    active_ref = get_active_tone_reference(session, segment)
    versions = get_tone_version_history(session, segment)
    rejections = get_tone_rejections(session, segment)
    synthetic_contact = get_synthetic_contact(segment)
    writer_preview = get_writer_preview_payload(session, segment)

    return {
        "segment": segment,
        "name": seg_obj.name,
        "target_words": f"{seg_obj.target_words_min}–{seg_obj.target_words_max}",
        "messages": studio_sess.messages or [],
        "active_tone": {
            "version": active_ref.version_number if active_ref else None,
            "draft_body": active_ref.draft_body if active_ref else None,
            "tone_notes": active_ref.tone_notes if active_ref else None,
            "actor": active_ref.actor if active_ref else None,
            "created_at": active_ref.created_at.isoformat() if active_ref and active_ref.created_at else None,
            "validator_results": active_ref.validator_results if active_ref else {},
        } if active_ref else None,
        "history": [
            {
                "id": v.id,
                "version_number": v.version_number,
                "status": v.status,
                "is_active": v.is_active,
                "tone_notes": v.tone_notes,
                "actor": v.actor,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "draft_excerpt": v.draft_body[:160] + ("..." if len(v.draft_body) > 160 else ""),
            }
            for v in versions
        ],
        "rejections": [
            {
                "id": r.id,
                "rejection_reason": r.rejection_reason,
                "actor": r.actor,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "draft_excerpt": r.draft_body[:160] + ("..." if len(r.draft_body) > 160 else ""),
            }
            for r in rejections
        ],
        "usage": usage,
        "synthetic_contact": synthetic_contact,
        "writer_preview": writer_preview,
        "quick_prompts": DEFAULT_QUICK_PROMPTS,
    }


@router.post("/{segment}/generate")
def generate_sample(
    segment: str,
    req: GenerateSampleRequest,
    session: Session = Depends(get_db),
):
    """
    Generates a sample outreach email for this segment matching the operator's tone instruction.
    - 10 generations per day limit per segment.
    - Fenced to TONE adjustments only (refuses format/price changes).
    - Synthetic contact data only.
    - Validates generated sample against segment rulebook.
    """
    try:
        result = generate_tone_sample(
            session=session,
            segment=segment,
            operator_instruction=req.instruction,
            operator_name=req.operator_name,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/{segment}/approve")
def approve_sample(
    segment: str,
    req: ApproveDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Approves draft as active tone reference for this segment.
    - Re-validates draft.
    - Archives previous version.
    - Records AuditLog row (AuditReason.TONE_APPROVED).
    """
    try:
        new_ref = approve_tone_reference(
            session=session,
            segment=segment,
            draft_body=req.draft_body,
            tone_notes=req.tone_notes,
            operator_name=req.operator_name,
        )
        writer_preview = get_writer_preview_payload(session, segment)
        return {
            "success": True,
            "version_number": new_ref.version_number,
            "message": f"Tone v{new_ref.version_number} approved and activated for segment '{segment}'.",
            "writer_preview": writer_preview,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{segment}/reject")
def reject_sample(
    segment: str,
    req: RejectDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Records draft rejection with optional reason.
    """
    try:
        rej = reject_tone_sample(
            session=session,
            segment=segment,
            draft_body=req.draft_body,
            reason=req.reason,
            operator_name=req.operator_name,
        )
        return {
            "success": True,
            "message": f"Draft rejected and recorded in rejections history.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{segment}/rollback/{version_number}")
def rollback_tone(
    segment: str,
    version_number: int,
    req: RollbackRequest,
    session: Session = Depends(get_db),
):
    """
    Restores an earlier approved version as the active tone reference.
    - Records AuditLog row (AuditReason.TONE_ROLLBACK).
    """
    try:
        target = rollback_tone_reference(
            session=session,
            segment=segment,
            version_number=version_number,
            operator_name=req.operator_name,
        )
        writer_preview = get_writer_preview_payload(session, segment)
        return {
            "success": True,
            "version_number": target.version_number,
            "message": f"Tone rolled back to v{target.version_number} successfully.",
            "writer_preview": writer_preview,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
