"""
Management & Directory Endpoints for Narayani Outreach UI (Phase 1 Read-Only).
Provides paginated, filtered views for Contacts, Queue, Suppression, Audit Log, System Config, and Needs Review.
Zero LLM calls, zero mutations.
"""

import os
import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text, func, or_
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.db.models import (
    Contact,
    OutboundQueue,
    Suppression,
    AuditLog,
    Segment,
    AuditReason,
    record_audit_log,
    Catalogue,
    ApprovedClaim,
    RulebookConfig,
)
from app.config import settings
from app.pipeline.cap_enforcer import DAILY_TOTAL_SYSTEM_LIMIT
from app.pipeline.providers.smtp_provider import SEED_ALLOWLIST, SMTPEmailProvider
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.workers.a3_reply_classifier.service import classify_inbound_reply
from app.validators.rulebook_checks import validate_rulebook_v0
from app.validators.commercial_term_gate import validate_commercial_terms
from app.pipeline.import_csv import import_prospects_csv
from app.pipeline.reason_formatter import format_hold_reason
from app.pipeline.catalogue_service import save_uploaded_catalogue, ensure_catalogues_dir
from app.pipeline.config_service import (
    create_approved_claim,
    upload_claims_csv,
    retire_claim,
    get_active_rulebook,
    update_rulebook,
)
from app.pipeline.dispatch_worker import dispatch_worker
from app.pipeline.playbook_service import (
    import_playbook_markdown,
    export_playbook_markdown,
    update_single_segment_playbook,
)
from app.pipeline.imap_listener import poll_imap_inbox, LAST_POLL_STATUS

logger = logging.getLogger("management_router")

router = APIRouter(tags=["Management & UI Data"])


def get_db():
    with get_db_session() as session:
        yield session


def mask_email(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return "[External / Deleted]"
    parts = email.split("@", 1)
    user, domain = parts[0], parts[1]
    if len(user) <= 1:
        masked_user = user + "***"
    else:
        masked_user = user[0] + "***"
    return f"{masked_user}@{domain}"


# ---------------------------------------------------------------------------
# 1. Contacts Directory
# ---------------------------------------------------------------------------

class ContactItemOut(BaseModel):
    id: int
    company_name: str
    company_domain: Optional[str] = None
    contact_name: str
    designation: Optional[str] = None
    country: Optional[str] = None
    email: str
    segment: str
    verification_status: str
    verification_reason: Optional[str] = None
    status: str
    opportunity_score: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContactsListResponse(BaseModel):
    items: List[ContactItemOut]
    total: int
    page: int
    page_size: int
    segments: List[str] = []


@router.get("/api/contacts", response_model=ContactsListResponse)
def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    segment: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    session: Session = Depends(get_db),
) -> ContactsListResponse:
    query = session.query(Contact)

    if status:
        query = query.filter(Contact.status == status)
    if segment:
        query = query.filter(Contact.segment == segment)
    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Contact.contact_name.ilike(search_pattern),
                Contact.company_name.ilike(search_pattern),
                Contact.email.ilike(search_pattern),
                Contact.company_domain.ilike(search_pattern),
            )
        )

    total = query.count()
    items = (
        query.order_by(Contact.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    all_segments = [s[0] for s in session.query(Segment.segment).order_by(Segment.segment).all()]

    return ContactsListResponse(
        items=[
            ContactItemOut(
                id=c.id,
                company_name=c.company_name,
                company_domain=c.company_domain,
                contact_name=c.contact_name,
                designation=c.designation,
                country=c.country,
                email=c.email,
                segment=c.segment,
                verification_status=c.verification_status,
                verification_reason=c.verification_reason,
                status=c.status,
                opportunity_score=c.opportunity_score,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in items
        ],
        total=total,
        page=page,
        page_size=page_size,
        segments=all_segments,
    )


# ---------------------------------------------------------------------------
# 2. Outbound Queue
# ---------------------------------------------------------------------------

class QueueItemDetailOut(BaseModel):
    id: int
    contact_id: int
    contact_name: str
    company_name: str
    email: str
    campaign_step: str
    status: str
    attempts: int
    locked_by: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    hold_reason: Optional[str] = None
    failed_validator: Optional[str] = None
    failed_validator_label: Optional[str] = None
    plain_label: Optional[str] = None
    plain_hold_reason: Optional[str] = None
    action_hint: Optional[str] = None
    attempt_history: List[Dict[str, Any]] = []
    is_veto: bool = False
    is_paused: bool = False
    rendered_subject: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class QueueListResponse(BaseModel):
    items: List[QueueItemDetailOut]
    total: int
    page: int
    page_size: int


@router.get("/api/queue", response_model=QueueListResponse)
def list_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    session: Session = Depends(get_db),
) -> QueueListResponse:
    query = (
        session.query(OutboundQueue, Contact)
        .join(Contact, OutboundQueue.contact_id == Contact.id)
    )

    if status:
        query = query.filter(OutboundQueue.status == status)

    total = query.count()
    rows = (
        query.order_by(OutboundQueue.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for q, c in rows:
        fmt = (
            format_hold_reason(
                hold_reason=q.hold_reason,
                failed_validator=q.failed_validator,
                attempts=q.attempts,
                status=q.status,
                attempt_history=q.attempt_history if isinstance(q.attempt_history, list) else [],
            )
            if (q.hold_reason or q.status in ("held", "vetoed", "waiting_for_ai"))
            else None
        )

        items.append(
            QueueItemDetailOut(
                id=q.id,
                contact_id=c.id,
                contact_name=c.contact_name,
                company_name=c.company_name,
                email=c.email,
                campaign_step=q.campaign_step,
                status=q.status,
                attempts=q.attempts,
                locked_by=q.locked_by,
                lease_expires_at=q.lease_expires_at,
                due_at=q.due_at,
                hold_reason=q.hold_reason,
                failed_validator=q.failed_validator,
                failed_validator_label=fmt["failed_validator_label"] if fmt else None,
                plain_label=fmt["plain_label"] if fmt else q.status.replace("_", " ").title(),
                plain_hold_reason=fmt["plain_reason"] if fmt else None,
                action_hint=fmt["action_hint"] if fmt else None,
                attempt_history=fmt["attempt_history"] if fmt else [],
                is_veto=fmt["is_veto"] if fmt else False,
                is_paused=fmt["is_paused"] if fmt else False,
                rendered_subject=q.rendered_subject,
                created_at=q.created_at,
                updated_at=q.updated_at,
            )
        )

    return QueueListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/api/queue/enqueue-pending")
def enqueue_pending_contacts(session: Session = Depends(get_db)):
    """
    Enqueues all pending, unsuppressed contacts into outbound_queue with status='eligible'.
    Guarantees that whenever an operator ingests or reviews prospects, they appear in Outbound Queue.
    """
    pending_contacts = (
        session.query(Contact)
        .filter(Contact.status == "pending", Contact.verification_status != "invalid")
        .all()
    )

    enqueued = []
    skipped = 0
    now = datetime.now(timezone.utc)

    for c in pending_contacts:
        email_hash = hashlib.sha256(c.email.strip().lower().encode("utf-8")).hexdigest()
        if session.query(Suppression).filter_by(email_hash=email_hash).first():
            skipped += 1
            continue

        existing_q = session.query(OutboundQueue).filter(
            OutboundQueue.contact_id == c.id,
            OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
        ).first()

        if existing_q:
            skipped += 1
            continue

        q_item = OutboundQueue(
            contact_id=c.id,
            campaign_step="intro",
            status="eligible",
            due_at=now,
            attempts=0,
            retention_class="sent_messages",
        )
        session.add(q_item)
        session.flush()

        record_audit_log(
            session,
            actor="operator",
            action="enqueue",
            object_type="queue_item",
            object_id=str(q_item.id),
            reason=AuditReason.INITIAL_ENQUEUE,
            new_state="eligible",
        )
        enqueued.append(q_item.id)

    session.commit()
    return {
        "success": True,
        "enqueued_count": len(enqueued),
        "skipped_count": skipped,
        "enqueued_ids": enqueued,
        "message": f"Successfully enqueued {len(enqueued)} prospects into the Outbound Queue ({skipped} skipped/already present).",
    }


class DispatchBatchRequest(BaseModel):
    limit: Optional[int] = 5


@router.get("/api/queue/worker-status")
def get_worker_status_endpoint():
    """Returns background outbound dispatch worker health and processing metrics."""
    return dispatch_worker.get_status()


@router.post("/api/queue/dispatch-next")
def dispatch_next_endpoint(session: Session = Depends(get_db)):
    """
    Manually triggers dispatch of the next eligible queue item.
    Enforces auto-stop, pause state, rate limits, and safe mock provider.
    """
    result = dispatch_worker.dispatch_single_now(session, worker_id="operator_manual")
    if result is None:
        return {"status": "no_task", "message": "No eligible tasks available in the queue to dispatch."}
    return {"status": "ok", "result": result}


@router.post("/api/queue/dispatch-batch")
def dispatch_batch_endpoint(
    request: Optional[DispatchBatchRequest] = None,
    session: Session = Depends(get_db),
):
    """
    Manually triggers dispatch of a batch of eligible queue items (up to limit).
    """
    limit = request.limit if request and request.limit else 5
    limit = max(1, min(limit, 20))  # Cap between 1 and 20
    results = dispatch_worker.dispatch_batch_now(session, limit=limit, worker_id="operator_manual")
    return {
        "status": "ok",
        "dispatched_count": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 3. Suppression Registry
# ---------------------------------------------------------------------------

class SuppressionItemOut(BaseModel):
    id: int
    email_hash: str
    email_hash_truncated: str
    contact_id: Optional[int] = None
    company_name: str
    masked_email: str
    reason: str
    created_by: str
    created_at: Optional[datetime] = None


class SuppressionListResponse(BaseModel):
    items: List[SuppressionItemOut]
    total: int
    page: int
    page_size: int


@router.get("/api/suppression", response_model=SuppressionListResponse)
def list_suppressions(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: Session = Depends(get_db),
) -> SuppressionListResponse:
    query = (
        session.query(Suppression, Contact)
        .outerjoin(Contact, Suppression.contact_id == Contact.id)
    )

    total = query.count()
    rows = (
        query.order_by(Suppression.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for supp, c in rows:
        h = supp.email_hash or ""
        truncated = (h[:12] + "...") if len(h) > 12 else h

        if c:
            company_label = c.company_name
            masked = mask_email(c.email)
        else:
            company_label = "[External / Deleted]"
            masked = "[External / Deleted]"

        items.append(
            SuppressionItemOut(
                id=supp.id,
                email_hash=h,
                email_hash_truncated=truncated,
                contact_id=supp.contact_id,
                company_name=company_label,
                masked_email=masked,
                reason=supp.reason,
                created_by=supp.created_by,
                created_at=supp.created_at,
            )
        )

    return SuppressionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# 4. Audit Log (Content-Free Ledger)
# ---------------------------------------------------------------------------

class AuditLogItemOut(BaseModel):
    id: int
    timestamp: datetime
    actor: str
    action: str
    object_type: str
    object_id: str
    prev_state: Optional[str] = None
    new_state: Optional[str] = None
    reason: str
    rulebook_version: Optional[str] = None
    prompt_version: Optional[str] = None


class AuditLogListResponse(BaseModel):
    items: List[AuditLogItemOut]
    total: int
    page: int
    page_size: int
    actors: List[str] = []
    actions: List[str] = []
    reasons: List[str] = []


@router.get("/api/audit-log", response_model=AuditLogListResponse)
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    session: Session = Depends(get_db),
) -> AuditLogListResponse:
    query = session.query(AuditLog)

    if isinstance(actor, str) and actor.strip():
        query = query.filter(AuditLog.actor == actor.strip())
    if isinstance(action, str) and action.strip():
        query = query.filter(AuditLog.action == action.strip())
    if isinstance(reason, str) and reason.strip():
        r_str = reason.strip().lower()
        valid_reasons = {e.value for e in AuditReason}
        if r_str in valid_reasons:
            query = query.filter(AuditLog.reason == r_str)
        else:
            # Prevent PostgreSQL DataError on invalid enum value
            query = query.filter(text("1 = 0"))
    if isinstance(search, str) and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                AuditLog.actor.ilike(term),
                AuditLog.action.ilike(term),
                AuditLog.object_id.ilike(term),
                AuditLog.object_type.ilike(term),
            )
        )

    total = query.count()
    rows = (
        query.order_by(AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AuditLogItemOut(
            id=r.id,
            timestamp=r.timestamp,
            actor=r.actor,
            action=r.action,
            object_type=r.object_type,
            object_id=r.object_id,
            prev_state=r.prev_state,
            new_state=r.new_state,
            reason=r.reason.value if hasattr(r.reason, "value") else str(r.reason),
            rulebook_version=r.rulebook_version,
            prompt_version=r.prompt_version,
        )
        for r in rows
    ]

    # Distinct values for filters
    actors = [a[0] for a in session.query(AuditLog.actor).distinct().order_by(AuditLog.actor).all() if a[0]]
    actions = [a[0] for a in session.query(AuditLog.action).distinct().order_by(AuditLog.action).all() if a[0]]
    reasons = [e.value for e in AuditReason]

    return AuditLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        actors=actors,
        actions=actions,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# 5. System & Safety Config
# ---------------------------------------------------------------------------

class SystemConfigResponse(BaseModel):
    test_mode: bool
    live_sending: bool
    seed_sending_enabled: bool
    smtp_host: str
    smtp_user: str
    allowlist_count: int
    daily_cap: int
    target_daily_sends: int
    ramp_target: int
    system_hard_ceiling: int
    active_segments_count: int


@router.get("/api/system/config", response_model=SystemConfigResponse)
def get_system_config(session: Session = Depends(get_db)) -> SystemConfigResponse:
    cap = min(settings.target_daily_sends, settings.ramp_target, DAILY_TOTAL_SYSTEM_LIMIT)
    segments_count = session.query(Segment).count()

    return SystemConfigResponse(
        test_mode=settings.test_mode,
        live_sending=settings.live_email_sending,
        seed_sending_enabled=settings.seed_sending_enabled,
        smtp_host=settings.smtp_host,
        smtp_user=settings.smtp_gmail,
        allowlist_count=len(SEED_ALLOWLIST),
        daily_cap=cap,
        target_daily_sends=settings.target_daily_sends,
        ramp_target=settings.ramp_target,
        system_hard_ceiling=DAILY_TOTAL_SYSTEM_LIMIT,
        active_segments_count=segments_count,
    )


# ---------------------------------------------------------------------------
# 6. Needs Review Detailed Endpoints
# ---------------------------------------------------------------------------

class HeldDraftDetailOut(BaseModel):
    id: int
    contact_id: int
    contact_name: str
    company_name: str
    email: str
    segment: str
    campaign_step: str
    rendered_subject: Optional[str] = None
    rendered_body: Optional[str] = None
    attempts: int
    hold_reason: Optional[str] = None
    failed_validator: Optional[str] = None
    failed_validator_label: str = "Quality Check"
    plain_label: str = "Held"
    plain_hold_reason: str = "Held: Quality check did not pass"
    action_hint: str = "Click 'Edit & Approve' to correct draft, or dismiss."
    attempt_history: List[Dict[str, Any]] = []
    is_veto: bool = False
    is_paused: bool = False
    is_dead_letter: bool = False
    category_label: Optional[str] = "Emails that couldn't be written correctly"
    lease_expiry_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class HeldContactDetailOut(BaseModel):
    id: int
    contact_name: str
    company_name: str
    email: str
    segment: str
    notes: Optional[str] = None
    product_interest: Optional[str] = None
    verification_status: str
    inbound_intent: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class NeedsReviewDetailResponse(BaseModel):
    draft_holds: List[HeldDraftDetailOut]
    contact_replies: List[HeldContactDetailOut]
    total_review_count: int


@router.get("/api/review/items", response_model=NeedsReviewDetailResponse)
def get_needs_review_items(session: Session = Depends(get_db)) -> NeedsReviewDetailResponse:
    """Returns separated sets of draft holds (held, vetoed, dead_letter) and inbound prospect replies."""
    held_draft_rows = (
        session.query(OutboundQueue, Contact)
        .join(Contact, OutboundQueue.contact_id == Contact.id)
        .filter(OutboundQueue.status.in_(["held", "vetoed", "dead_letter"]))
        .order_by(OutboundQueue.updated_at.desc())
        .all()
    )

    draft_holds = []
    for q, c in held_draft_rows:
        fmt = format_hold_reason(
            hold_reason=q.hold_reason,
            failed_validator=q.failed_validator,
            attempts=q.attempts,
            status=q.status,
            attempt_history=q.attempt_history if isinstance(q.attempt_history, list) else [],
        )
        draft_holds.append(
            HeldDraftDetailOut(
                id=q.id,
                contact_id=c.id,
                contact_name=c.contact_name,
                company_name=c.company_name,
                email=c.email,
                segment=c.segment,
                campaign_step=q.campaign_step,
                rendered_subject=q.rendered_subject,
                rendered_body=q.rendered_body,
                attempts=q.attempts if q.attempts > 0 else 1,
                hold_reason=q.hold_reason,
                failed_validator=q.failed_validator,
                failed_validator_label=fmt["failed_validator_label"],
                plain_label=fmt["plain_label"],
                plain_hold_reason=fmt["plain_reason"],
                action_hint=fmt["action_hint"],
                attempt_history=fmt["attempt_history"],
                is_veto=fmt["is_veto"],
                is_paused=fmt["is_paused"],
                is_dead_letter=fmt.get("is_dead_letter", False),
                category_label=fmt.get("category_label", "Emails that couldn't be written correctly"),
                lease_expiry_count=q.lease_expiry_count or 0,
                created_at=q.created_at,
                updated_at=q.updated_at,
            )
        )

    held_contacts = (
        session.query(Contact)
        .filter(Contact.status == "held")
        .order_by(Contact.updated_at.desc())
        .all()
    )

    contact_replies = []
    for c in held_contacts:
        intent = None
        if c.notes:
            if "[Inbound Reply - " in c.notes:
                try:
                    intent = c.notes.split("[Inbound Reply - ")[1].split("]")[0]
                except Exception:
                    pass
            elif "[A3 Low Confidence" in c.notes:
                intent = "low_confidence"
        contact_replies.append(
            HeldContactDetailOut(
                id=c.id,
                contact_name=c.contact_name,
                company_name=c.company_name,
                email=c.email,
                segment=c.segment,
                notes=c.notes,
                product_interest=c.product_interest,
                verification_status=c.verification_status,
                inbound_intent=intent,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )

    return NeedsReviewDetailResponse(
        draft_holds=draft_holds,
        contact_replies=contact_replies,
        total_review_count=len(draft_holds) + len(contact_replies),
    )


@router.get("/api/review/drafts/{draft_id}", response_model=HeldDraftDetailOut)
def get_held_draft_detail(draft_id: int, session: Session = Depends(get_db)) -> HeldDraftDetailOut:
    """Returns full details (including rendered_body and plain-language reason) of a single held/vetoed draft."""
    row = (
        session.query(OutboundQueue, Contact)
        .join(Contact, OutboundQueue.contact_id == Contact.id)
        .filter(OutboundQueue.id == draft_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Queue draft not found")

    q, c = row
    fmt = format_hold_reason(
        hold_reason=q.hold_reason,
        failed_validator=q.failed_validator,
        attempts=q.attempts,
        status=q.status,
        attempt_history=q.attempt_history if isinstance(q.attempt_history, list) else [],
    )

    return HeldDraftDetailOut(
        id=q.id,
        contact_id=c.id,
        contact_name=c.contact_name,
        company_name=c.company_name,
        email=c.email,
        segment=c.segment,
        campaign_step=q.campaign_step,
        rendered_subject=q.rendered_subject,
        rendered_body=q.rendered_body,
        attempts=q.attempts if q.attempts > 0 else 1,
        hold_reason=q.hold_reason,
        failed_validator=q.failed_validator,
        failed_validator_label=fmt["failed_validator_label"],
        plain_label=fmt["plain_label"],
        plain_hold_reason=fmt["plain_reason"],
        action_hint=fmt["action_hint"],
        attempt_history=fmt["attempt_history"],
        is_veto=fmt["is_veto"],
        is_paused=fmt["is_paused"],
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


# ---------------------------------------------------------------------------
# Phase 2: Human Operator Actions & Mutations
# ---------------------------------------------------------------------------

class ApproveDraftRequest(BaseModel):
    operator_name: str = "operator"
    subject: str
    body: str


class RetryDraftRequest(BaseModel):
    operator_name: str = "operator"


class DismissDraftRequest(BaseModel):
    operator_name: str = "operator"
    reason: Optional[str] = "dismissed_by_operator"


class MarkReplyHandledRequest(BaseModel):
    operator_name: str = "operator"
    notes: Optional[str] = None


class SendOperatorReplyRequest(BaseModel):
    operator_name: str = Field("operator", description="Operator name sending the reply")
    subject: str = Field(..., description="Subject of the email reply")
    body: str = Field(..., description="Email reply text body")
    mark_handled: bool = Field(True, description="Whether to transition contact back to 'contacted'")
    notes: Optional[str] = Field(None, description="Optional internal note")


class SimulateInboundReplyRequest(BaseModel):
    reply_body: str = Field(..., description="Raw text of the inbound reply")
    subject: Optional[str] = Field(None, description="Subject of the inbound reply")
    headers: Optional[Dict[str, str]] = Field(None, description="Inbound email headers")


class UnsuppressRequest(BaseModel):
    operator_name: str
    reason: str


class CsvImportPayload(BaseModel):
    csv_text: str
    filename: Optional[str] = "import.csv"


@router.post("/api/review/{queue_id}/approve")
def edit_and_approve_draft(
    queue_id: int,
    req: ApproveDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Validates edited draft against Rulebook checks and Commercial Term gates.
    If valid, updates copy, resets hold to eligible, and records an AuditLog row.
    If invalid, returns HTTP 422 with validation errors, blocking the save.
    """
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    contact = session.query(Contact).filter(Contact.id == queue_item.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Associated contact not found")

    segment_obj = session.query(Segment).filter(Segment.segment == contact.segment).first()
    w_min = segment_obj.target_words_min if segment_obj else 80
    w_max = segment_obj.target_words_max if segment_obj else 120

    first_name = contact.contact_name.strip().split()[0] if contact.contact_name else "there"

    # Deterministic Rulebook validation (VISION.md §6.2)
    rulebook_res = validate_rulebook_v0(
        body=req.body,
        first_name=first_name,
        word_count_min=w_min,
        word_count_max=w_max,
    )

    # Deterministic Commercial Term Gate validation (VISION.md §6.4)
    comm_res = validate_commercial_terms(
        body=req.body,
        subject=req.subject,
    )

    all_errors = []
    if not rulebook_res.passed:
        all_errors.extend(rulebook_res.errors)
    if not comm_res.passed:
        all_errors.extend(comm_res.violations)

    if all_errors:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "passed": False,
                "errors": all_errors,
                "message": "Draft validation failed. Inline corrections required before approving.",
            },
        )

    # All checks passed: apply edits and requeue
    prev_state = queue_item.status
    queue_item.rendered_subject = req.subject.strip()
    queue_item.rendered_body = req.body.strip()
    queue_item.status = "eligible"
    queue_item.hold_reason = None
    queue_item.attempts = 0

    operator = req.operator_name.strip() or "human_operator"

    record_audit_log(
        session,
        actor=operator,
        action="approve_draft",
        object_type="queue_item",
        object_id=str(queue_item.id),
        prev_state=prev_state,
        new_state="eligible",
        reason=AuditReason.ADMIN_PAUSE,
    )

    session.commit()
    return {
        "success": True,
        "passed": True,
        "message": f"Draft #{queue_id} successfully validated, approved, and enqueued as eligible.",
    }


@router.post("/api/review/{queue_id}/retry")
def retry_held_draft(
    queue_id: int,
    req: Optional[RetryDraftRequest] = None,
    session: Session = Depends(get_db),
):
    """
    Clears hold_reason and returns queue item to eligible status for worker pickup.
    """
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    prev_state = queue_item.status
    queue_item.status = "eligible"
    queue_item.hold_reason = None
    queue_item.attempts = 0

    operator = (req.operator_name.strip() if req and req.operator_name else "human_operator")

    record_audit_log(
        session,
        actor=operator,
        action="retry_draft",
        object_type="queue_item",
        object_id=str(queue_item.id),
        prev_state=prev_state,
        new_state="eligible",
        reason=AuditReason.ADMIN_PAUSE,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Draft #{queue_id} hold cleared and returned to eligible.",
    }


@router.post("/api/review/{queue_id}/dismiss")
def dismiss_held_draft(
    queue_id: int,
    req: DismissDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Cancels held draft, setting status to cancelled with audit record.
    """
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    prev_state = queue_item.status
    queue_item.status = "cancelled"
    queue_item.hold_reason = req.reason or "dismissed_by_operator"

    operator = req.operator_name.strip() or "human_operator"

    record_audit_log(
        session,
        actor=operator,
        action="dismiss_draft",
        object_type="queue_item",
        object_id=str(queue_item.id),
        prev_state=prev_state,
        new_state="cancelled",
        reason=AuditReason.ADMIN_PAUSE,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Draft #{queue_id} dismissed and marked cancelled.",
    }


@router.post("/api/review/{queue_id}/skip")
def skip_held_draft(
    queue_id: int,
    req: DismissDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Operator action: skip this person.
    Marks queue item cancelled/skipped, records audit row.
    """
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    prev_state = queue_item.status
    queue_item.status = "cancelled"
    queue_item.hold_reason = req.reason or "Skipped by operator"

    operator = req.operator_name.strip() or "human_operator"

    record_audit_log(
        session,
        actor=operator,
        action="skip_person",
        object_type="queue_item",
        object_id=str(queue_item.id),
        prev_state=prev_state,
        new_state="cancelled",
        reason=AuditReason.ADMIN_PAUSE,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Prospect in queue item #{queue_id} skipped.",
    }


@router.post("/api/review/{queue_id}/do-not-contact")
def mark_do_not_contact(
    queue_id: int,
    req: DismissDraftRequest,
    session: Session = Depends(get_db),
):
    """
    Operator action: mark do-not-contact.
    Suppresses contact, cancels queue item, writes exactly 1 audit row.
    """
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
    if not queue_item:
        raise HTTPException(status_code=404, detail="Queue item not found")

    contact = session.query(Contact).filter(Contact.id == queue_item.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    import hashlib
    email_hash = hashlib.sha256(contact.email.lower().strip().encode("utf-8")).hexdigest()
    existing_supp = session.query(Suppression).filter(Suppression.email_hash == email_hash).first()
    if not existing_supp:
        supp = Suppression(
            email_hash=email_hash,
            contact_id=contact.id,
            reason="operator_do_not_contact",
            created_by="operator",
            retention_class="permanent",
        )
        session.add(supp)

    contact.status = "suppressed"
    prev_state = queue_item.status
    queue_item.status = "cancelled"
    queue_item.hold_reason = "Marked do-not-contact by operator"

    operator = req.operator_name.strip() or "human_operator"

    record_audit_log(
        session,
        actor=operator,
        action="mark_do_not_contact",
        object_type="contact",
        object_id=str(contact.id),
        prev_state=prev_state,
        new_state="suppressed",
        reason=AuditReason.ADMIN_PAUSE,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Contact #{contact.id} ({contact.email}) added to suppression list.",
    }


@router.post("/api/system/recovery/run")
def run_recovery_check_now(session: Session = Depends(get_db)):
    """
    Manual 'Run recovery check now' button.
    Uses the exact same locked code path (SELECT ... FOR UPDATE SKIP LOCKED).
    """
    from app.pipeline.sweeper import run_sweeper
    res = run_sweeper(session)
    return {
        "status": "ok",
        "message": f"Recovery check completed. {res['recovered_count']} items recovered.",
        "recovered_count": res["recovered_count"],
        "last_sweep_at": res["last_sweep_at"],
        "details": res["details"],
    }


@router.get("/api/system/health")
def get_system_health(session: Session = Depends(get_db)):
    """
    System health status endpoint:
    Returns recovery sweeper health, Groq connection status, sending status, and numbers.
    Turns status_color to 'red' if last sweep is older than 3 intervals or key rejected.
    """
    from app.pipeline.sweeper import get_sweeper_health
    from app.core.groq_provider import circuit_breaker
    from app.pipeline.sending_controller import get_reconciled_numbers, get_sending_status

    sweeper_info = get_sweeper_health(session)
    sending_info = get_sending_status(session)
    reconciled = get_reconciled_numbers(session)

    key_rejected = circuit_breaker.is_key_rejected()
    daily_hit = circuit_breaker.is_daily_limit_hit()

    if key_rejected:
        banner_color = "red"
        banner_message = "Groq rejected the key. Update it in Connections."
    elif daily_hit:
        banner_color = "yellow"
        banner_message = "Groq daily token allowance reached. Outreach will resume after reset at 00:00 UTC."
    elif sweeper_info.get("is_stale"):
        banner_color = "red"
        banner_message = f"Recovery sweeper is stale: {sweeper_info.get('message')}"
    else:
        banner_color = "green"
        banner_message = "All background systems operational."

    is_healthy = not (key_rejected or sweeper_info.get("is_stale"))
    return {
        "status": "warning" if not is_healthy else "operational",
        "healthy": is_healthy,
        "groq_key_rejected": key_rejected,
        "banner": {
            "color": banner_color,
            "message": banner_message,
            "key_rejected": key_rejected,
            "daily_limit_reached": daily_hit,
        },
        "sweeper": sweeper_info,
        "sending": sending_info,
        "reconciled_numbers": reconciled,
    }



@router.post("/api/review/contacts/{contact_id}/handled")
def mark_reply_handled(
    contact_id: int,
    req: MarkReplyHandledRequest,
    session: Session = Depends(get_db),
):
    """
    Transitions contact from held back to contacted after human takeover.
    """
    contact = session.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    prev_state = contact.status
    contact.status = "contacted"
    if req.notes:
        contact.notes = f"{contact.notes or ''}\n[Operator Note]: {req.notes}".strip()

    operator = req.operator_name.strip() or "human_operator"

    record_audit_log(
        session,
        actor=operator,
        action="reply_handled",
        object_type="contact",
        object_id=str(contact.id),
        prev_state=prev_state,
        new_state="contacted",
        reason=AuditReason.HUMAN_REPLY,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Contact #{contact_id} marked handled and status set to contacted.",
    }


@router.post("/api/review/contacts/{contact_id}/reply")
def send_operator_reply(
    contact_id: int,
    req: SendOperatorReplyRequest,
    session: Session = Depends(get_db),
):
    """
    Allows the human operator to reply directly to a prospect email from the UI.
    Dispatches outbound email through the configured provider, updates contact status,
    appends the thread history to notes, and records an OPERATOR_EMAIL_SENT audit log.
    """
    contact = session.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    if contact.status == "suppressed":
        raise HTTPException(status_code=400, detail="Cannot send email to a suppressed contact.")

    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Reply body cannot be empty.")
    if not req.subject.strip():
        raise HTTPException(status_code=400, detail="Subject cannot be empty.")

    # Check test allowlist if in test mode
    if not settings.live_email_sending:
        allowlist_lower = [e.lower() for e in SEED_ALLOWLIST]
        if contact.email.lower() not in allowlist_lower:
            raise HTTPException(
                status_code=403,
                detail=f"Safe Test Mode: Outbound emails are restricted to the {len(SEED_ALLOWLIST)} verified test allowlist addresses.",
            )

    if settings.live_email_sending:
        provider = SMTPEmailProvider()
    else:
        provider = MockEmailProvider()

    try:
        result = provider.send_email(
            to_email=contact.email,
            subject=req.subject.strip(),
            body_text=req.body.strip(),
            session=session,
            contact_id=contact.id,
        )
    except Exception as exc:
        logger.error(f"Failed to dispatch operator reply to {contact.email}: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"Email delivery provider error: {str(exc)}",
        )

    if not result.success:
        logger.error(f"Email delivery failed for {contact.email}: {result.error}")
        raise HTTPException(
            status_code=502,
            detail=f"Email delivery failed: {result.error or 'Provider error'}",
        )

    prev_status = contact.status
    if req.mark_handled:
        contact.status = "contacted"

    operator = req.operator_name.strip() or "operator"
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    contact.notes = f"{contact.notes or ''}\n[Operator Reply by {operator} on {timestamp_str}]: {req.subject.strip()}".strip()
    if req.notes:
        contact.notes = f"{contact.notes}\n[Internal Note]: {req.notes.strip()}".strip()

    record_audit_log(
        session,
        actor=operator,
        action="send_operator_reply",
        object_type="contact",
        object_id=str(contact.id),
        prev_state=prev_status,
        new_state=contact.status,
        reason=AuditReason.OPERATOR_EMAIL_SENT,
    )

    session.commit()
    return {
        "success": True,
        "message": f"Email successfully dispatched to {contact.email}",
        "message_id": result.provider_message_id,
        "contact_status": contact.status,
    }


@router.post("/api/review/contacts/{contact_id}/simulate-reply")
def simulate_inbound_reply(
    contact_id: int,
    req: SimulateInboundReplyRequest,
    session: Session = Depends(get_db),
):
    """
    Ingests an inbound reply for a contact and runs the A3 classifier pipeline
    (prefilter -> Groq LLM -> mechanical overrides).
    """
    contact = session.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    final_contract, actions = classify_inbound_reply(
        session=session,
        contact=contact,
        body=req.reply_body,
        subject=req.subject,
        headers=req.headers,
    )

    return {
        "success": True,
        "contract": final_contract.model_dump(),
        "actions_taken": actions,
        "contact_status": contact.status,
    }


@router.post("/api/inbound/poll")
def poll_inbound_replies_endpoint(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    """
    On-demand trigger for polling Gmail IMAP inbox.
    Retrieves unread messages, matches sender against contacts directory,
    and runs the A3 classifier pipeline.
    """
    res = poll_imap_inbox(session=session, limit=limit)
    return res


@router.get("/api/inbound/status")
def get_inbound_poller_status():
    """
    Returns the current health and status of the IMAP inbound poller.
    """
    return {
        "status": LAST_POLL_STATUS.get("status", "idle"),
        "last_poll_at": LAST_POLL_STATUS.get("last_poll_at"),
        "last_error": LAST_POLL_STATUS.get("last_error"),
        "last_result": LAST_POLL_STATUS.get("last_result"),
        "configured": bool(settings.smtp_gmail and settings.smtp_password),
        "account": settings.smtp_gmail,
    }


@router.post("/api/suppression/{suppression_id}/unsuppress")
def unsuppress_contact(
    suppression_id: int,
    req: UnsuppressRequest,
    session: Session = Depends(get_db),
):
    """
    Human-only un-suppression action (§14.8b, §18.2).
    Requires explicit operator name and reason, writes UNSUPPRESSION audit log, and deletes suppression row.
    """
    if not req.operator_name.strip():
        raise HTTPException(status_code=400, detail="Operator name is required for un-suppression.")
    if not req.reason.strip():
        raise HTTPException(status_code=400, detail="Explicit reason is required for un-suppression (§14.8b).")

    supp = session.query(Suppression).filter(Suppression.id == suppression_id).first()
    if not supp:
        raise HTTPException(status_code=404, detail="Suppression record not found")

    # If linked contact was suppressed, restore to pending
    if supp.contact_id:
        contact = session.query(Contact).filter(Contact.id == supp.contact_id).first()
        if contact and contact.status == "suppressed":
            contact.status = "pending"
    else:
        # Check by email_hash if contact_id was not directly linked
        contacts = session.query(Contact).filter(Contact.status == "suppressed").all()
        for c in contacts:
            if hashlib.sha256(c.email.strip().lower().encode("utf-8")).hexdigest() == supp.email_hash:
                c.status = "pending"

    operator = req.operator_name.strip()

    record_audit_log(
        session,
        actor=operator,
        action="unsuppress",
        object_type="suppression",
        object_id=str(supp.id),
        prev_state="suppressed",
        new_state="active",
        reason=AuditReason.UNSUPPRESSION,
    )

    session.delete(supp)
    session.commit()
    return {
        "success": True,
        "message": f"Suppression #{suppression_id} successfully un-suppressed by {operator}.",
    }


@router.post("/api/contacts/import-csv")
async def import_csv_endpoint(
    request: Request,
    session: Session = Depends(get_db),
):
    """
    Imports contacts from CSV text or payload using import_prospects_csv (§18.3).
    Enforces segment requirement, suppression checking, and verification stages.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        csv_text = payload.get("csv_text", "")
    else:
        body_bytes = await request.body()
        csv_text = body_bytes.decode("utf-8-sig", errors="replace")

    if not csv_text.strip():
        raise HTTPException(status_code=400, detail="CSV content is empty.")

    report = import_prospects_csv(
        session=session,
        csv_source=csv_text,
        suppress_check=True,
        verify_emails=True,
    )

    return {
        "success": True,
        "total_rows": report.total_rows,
        "imported_count": report.imported_count,
        "enqueued_count": report.enqueued_count,
        "inserted_contacts": report.imported_count,
        "rejected_count": report.rejected_count,
        "rejected_rows": [
            {"row_num": r.row_num, "error": r.error, "data": r.data}
            for r in report.rejected_rows[:20]
        ],
    }


# ---------------------------------------------------------------------------
# Phase 9: Operator-Uploaded Catalogues, Claims & Rulebooks (§Phase 9)
# ---------------------------------------------------------------------------

class ToggleCatalogueRequest(BaseModel):
    enabled: bool
    operator_name: Optional[str] = "operator"


@router.get("/api/segments/catalogue-status")
def get_segments_catalogue_status(session: Session = Depends(get_db)):
    """
    Returns catalogue upload and attachment toggle status for all customer segments.
    """
    segments = session.query(Segment).order_by(Segment.name).all()
    result = []
    cat_dir = ensure_catalogues_dir()
    for seg in segments:
        cat_info = None
        if seg.catalogue_id:
            cat = session.query(Catalogue).filter(Catalogue.id == seg.catalogue_id).first()
            if cat:
                file_exists = False
                if cat.catalogue_file:
                    file_exists = os.path.exists(os.path.join(cat_dir, cat.catalogue_file))
                cat_info = {
                    "id": cat.id,
                    "name": cat.name,
                    "checksum": cat.checksum,
                    "file_size_bytes": cat.file_size_bytes,
                    "size_mb": round(cat.file_size_bytes / (1024 * 1024), 2) if cat.file_size_bytes else None,
                    "file_exists": file_exists,
                }
        result.append({
            "segment": seg.segment,
            "name": seg.name,
            "attach_catalogue": seg.attach_catalogue,
            "catalogue": cat_info,
        })
    return result


@router.post("/api/catalogues/upload")
async def upload_catalogue_endpoint(
    request: Request,
    session: Session = Depends(get_db),
    file: Optional[UploadFile] = File(None),
    segment_id: Optional[str] = Form(None),
    operator_name: Optional[str] = Form("operator"),
):
    """
    Uploads a catalogue PDF per segment from the website UI.
    Verifies magic bytes (%PDF-), computes sha256 checksum, stores to disk,
    links to Segment, and audits action.
    """
    content_type = request.headers.get("content-type", "")
    file_bytes = None
    filename = "catalogue.pdf"
    op_name = operator_name or "operator"
    seg_id = segment_id

    if file is not None:
        file_bytes = await file.read()
        filename = file.filename or "catalogue.pdf"
    elif "application/json" in content_type:
        payload = await request.json()
        seg_id = payload.get("segment_id")
        op_name = payload.get("operator_name", "operator")
        filename = payload.get("filename", "catalogue.pdf")
        b64_data = payload.get("file_b64")
        if b64_data:
            try:
                file_bytes = base64.b64decode(b64_data)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid base64 encoding for file.")
    else:
        form = await request.form()
        form_file = form.get("file")
        seg_id = seg_id or form.get("segment_id")
        op_name = form.get("operator_name") or op_name
        if form_file and hasattr(form_file, "read"):
            file_bytes = await form_file.read()
            filename = getattr(form_file, "filename", "catalogue.pdf")

    if not seg_id:
        raise HTTPException(status_code=400, detail="segment_id is required.")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="No catalogue file uploaded or file is empty.")

    try:
        result = save_uploaded_catalogue(
            file_bytes=file_bytes,
            original_filename=filename,
            segment_id=seg_id,
            operator_name=op_name,
            session=session,
        )
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error uploading catalogue: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/segments/{segment_id}/toggle-catalogue")
def toggle_segment_catalogue(
    segment_id: str,
    req: ToggleCatalogueRequest,
    session: Session = Depends(get_db),
):
    """
    Toggles whether catalogue PDF is attached for a specific segment.
    """
    seg = session.query(Segment).filter(Segment.segment == segment_id).first()
    if not seg:
        raise HTTPException(status_code=404, detail=f"Segment '{segment_id}' not found.")

    prev_state = seg.attach_catalogue
    seg.attach_catalogue = req.enabled

    record_audit_log(
        session=session,
        actor=req.operator_name or "operator",
        action="catalogue_attachment_toggled",
        object_type="segment",
        object_id=segment_id,
        reason=AuditReason.CATALOGUE_ATTACHMENT_TOGGLED,
        prev_state=str(prev_state),
        new_state=str(req.enabled),
    )
    session.commit()
    return {
        "success": True,
        "segment": segment_id,
        "attach_catalogue": seg.attach_catalogue,
        "message": f"Catalogue attachment for segment '{seg.name}' is now {'enabled' if req.enabled else 'disabled'}.",
    }


@router.get("/api/catalogues/download/{catalogue_id}")
def download_catalogue_pdf(
    catalogue_id: int,
    session: Session = Depends(get_db),
):
    """
    Serves catalogue PDF file for direct download or fallback link.
    """
    cat = session.query(Catalogue).filter(Catalogue.id == catalogue_id).first()
    if not cat or not cat.catalogue_file:
        raise HTTPException(status_code=404, detail="Catalogue not found.")

    cat_dir = ensure_catalogues_dir()
    file_path = os.path.join(cat_dir, cat.catalogue_file)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Catalogue PDF file not found on disk.")

    download_name = f"{cat.name.replace(' ', '_')}.pdf" if cat.name else "catalogue.pdf"
    return FileResponse(file_path, media_type="application/pdf", filename=download_name)


# ---------------------------------------------------------------------------
# Approved Claims Management (§Phase 9)
# ---------------------------------------------------------------------------

class CreateClaimRequest(BaseModel):
    segment: str
    product_category: Optional[str] = "general"
    claim_type: Optional[str] = "standard"
    text: str
    evidence_note: Optional[str] = None
    operator_name: Optional[str] = "operator"


@router.get("/api/claims")
def list_claims(
    segment: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    """
    Lists approved claims with optional segment and active status filtering.
    """
    query = session.query(ApprovedClaim)
    if segment:
        query = query.filter(ApprovedClaim.segment == segment)
    if is_active is not None:
        query = query.filter(ApprovedClaim.is_active == is_active)

    total = query.count()
    claims = query.order_by(ApprovedClaim.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": c.id,
                "segment": c.segment,
                "product_category": c.product_category,
                "claim_type": c.claim_type,
                "text": c.text,
                "evidence_note": c.evidence_note,
                "is_active": c.is_active,
                "created_at": c.created_at,
                "valid_from": c.valid_from,
                "valid_to": c.valid_to,
            }
            for c in claims
        ],
    }


@router.post("/api/claims")
def create_claim_endpoint(
    req: CreateClaimRequest,
    session: Session = Depends(get_db),
):
    """
    Creates an approved claim. Validated against commercial-term gate (VISION.md §10.8).
    """
    try:
        claim = create_approved_claim(
            session=session,
            segment=req.segment,
            product_category=req.product_category or "general",
            claim_type=req.claim_type or "standard",
            text=req.text,
            evidence_note=req.evidence_note,
            operator_name=req.operator_name or "operator",
        )
        return {
            "success": True,
            "claim": {
                "id": claim.id,
                "segment": claim.segment,
                "product_category": claim.product_category,
                "claim_type": claim.claim_type,
                "text": claim.text,
                "evidence_note": claim.evidence_note,
                "is_active": claim.is_active,
            },
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error creating approved claim: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/claims/upload")
async def upload_claims_endpoint(
    request: Request,
    session: Session = Depends(get_db),
    file: Optional[UploadFile] = File(None),
    operator_name: Optional[str] = Form("operator"),
):
    """
    Uploads approved claims from CSV file or payload.
    Validates each claim against commercial-term gate before inserting.
    """
    content_type = request.headers.get("content-type", "")
    csv_text = ""
    op_name = operator_name or "operator"

    if file is not None:
        content_bytes = await file.read()
        csv_text = content_bytes.decode("utf-8-sig", errors="replace")
    elif "application/json" in content_type:
        payload = await request.json()
        csv_text = payload.get("csv_content", "")
        op_name = payload.get("operator_name", "operator")
    else:
        form = await request.form()
        form_file = form.get("file")
        op_name = form.get("operator_name") or op_name
        if form_file and hasattr(form_file, "read"):
            content_bytes = await form_file.read()
            csv_text = content_bytes.decode("utf-8-sig", errors="replace")
        elif "csv_content" in form:
            csv_text = str(form.get("csv_content"))
        else:
            body_bytes = await request.body()
            csv_text = body_bytes.decode("utf-8-sig", errors="replace")

    if not csv_text.strip():
        raise HTTPException(status_code=400, detail="CSV content is empty.")

    result = upload_claims_csv(session=session, csv_content=csv_text, operator_name=op_name)
    return {
        "success": True,
        **result,
    }


@router.delete("/api/claims/{claim_id}")
def retire_claim_endpoint(
    claim_id: int,
    operator_name: str = Query("operator"),
    session: Session = Depends(get_db),
):
    """
    Retires an approved claim (soft delete / is_active = False).
    Claims are retired, never deleted (§14.8).
    """
    success = retire_claim(session=session, claim_id=claim_id, operator_name=operator_name)
    if not success:
        raise HTTPException(status_code=404, detail="Claim not found.")
    return {"success": True, "message": f"Claim #{claim_id} retired."}


# ---------------------------------------------------------------------------
# Dynamic Rulebook Management (§Phase 9)
# ---------------------------------------------------------------------------

class UpdateRulebookRequest(BaseModel):
    name: Optional[str] = "House Rulebook"
    banned_openers: List[str] = Field(default_factory=list)
    banned_phrases: List[str] = Field(default_factory=list)
    max_paragraphs: int = 3
    max_sentences_per_paragraph: int = 3
    allow_exclamation: bool = False
    allow_all_caps: bool = False
    operator_name: Optional[str] = "operator"


@router.get("/api/rulebook")
def get_rulebook_endpoint(session: Session = Depends(get_db)):
    """
    Returns the currently active rulebook configuration.
    """
    rb = get_active_rulebook(session)
    return {
        "id": rb.id,
        "version": rb.version,
        "name": rb.name,
        "is_active": rb.is_active,
        "banned_openers": rb.banned_openers,
        "banned_phrases": rb.banned_phrases,
        "max_paragraphs": rb.max_paragraphs,
        "max_sentences_per_paragraph": rb.max_sentences_per_paragraph,
        "allow_exclamation": rb.allow_exclamation,
        "allow_all_caps": rb.allow_all_caps,
        "created_at": rb.created_at,
        "created_by": rb.created_by,
    }


@router.post("/api/rulebook")
def update_rulebook_endpoint(
    req: UpdateRulebookRequest,
    session: Session = Depends(get_db),
):
    """
    Updates rulebook settings: creates a new active version and audits the change.
    """
    new_rb = update_rulebook(
        session=session,
        name=req.name or "House Rulebook",
        banned_openers=req.banned_openers,
        banned_phrases=req.banned_phrases,
        max_paragraphs=req.max_paragraphs,
        max_sentences_per_paragraph=req.max_sentences_per_paragraph,
        allow_exclamation=req.allow_exclamation,
        allow_all_caps=req.allow_all_caps,
        operator_name=req.operator_name or "operator",
    )
    return {
        "success": True,
        "rulebook": {
            "id": new_rb.id,
            "version": new_rb.version,
            "name": new_rb.name,
            "is_active": new_rb.is_active,
            "banned_openers": new_rb.banned_openers,
            "banned_phrases": new_rb.banned_phrases,
            "max_paragraphs": new_rb.max_paragraphs,
            "max_sentences_per_paragraph": new_rb.max_sentences_per_paragraph,
            "allow_exclamation": new_rb.allow_exclamation,
            "allow_all_caps": new_rb.allow_all_caps,
            "created_at": new_rb.created_at,
            "created_by": new_rb.created_by,
        },
    }


# ---------------------------------------------------------------------------
# Guarded Playbook & Dynamic Rulebook Sync API (§Phase 2)
# ---------------------------------------------------------------------------

class UpdateSegmentPlaybookRequest(BaseModel):
    target_words_min: Optional[int] = None
    target_words_max: Optional[int] = None
    allowed_angles: Optional[List[str]] = None
    allowed_ctas: Optional[List[str]] = None
    forbidden_topics: Optional[List[str]] = None
    register_spec: Optional[str] = None
    operator_name: Optional[str] = "operator"


@router.get("/api/playbooks")
def get_playbooks_endpoint(session: Session = Depends(get_db)):
    """
    Returns all segment playbooks and active global rulebook.
    """
    segments = session.query(Segment).order_by(Segment.segment).all()
    rb = get_active_rulebook(session)
    return {
        "segments": [
            {
                "segment": s.segment,
                "name": s.name,
                "target_words_min": s.target_words_min,
                "target_words_max": s.target_words_max,
                "allowed_angles": s.allowed_angles or [],
                "allowed_ctas": s.allowed_ctas or [],
                "forbidden_topics": s.forbidden_topics or [],
                "register_spec": s.register_spec or "",
                "catalogue_id": s.catalogue_id,
                "attach_catalogue": s.attach_catalogue,
                "updated_at": s.updated_at,
            }
            for s in segments
        ],
        "global_rulebook": {
            "id": rb.id,
            "version": rb.version,
            "name": rb.name,
            "is_active": rb.is_active,
            "banned_openers": rb.banned_openers or [],
            "banned_phrases": rb.banned_phrases or [],
            "max_paragraphs": rb.max_paragraphs,
            "max_sentences_per_paragraph": rb.max_sentences_per_paragraph,
            "allow_exclamation": rb.allow_exclamation,
            "allow_all_caps": rb.allow_all_caps,
        },
    }


@router.put("/api/playbooks/{segment_id}")
def update_segment_playbook_endpoint(
    segment_id: str,
    req: UpdateSegmentPlaybookRequest,
    session: Session = Depends(get_db),
):
    """
    Guarded update for an individual segment's playbook parameters.
    """
    try:
        updated_seg = update_single_segment_playbook(
            session=session,
            segment_id=segment_id,
            updates=req.model_dump(exclude_unset=True),
            operator_name=req.operator_name or "operator",
        )
        return {
            "success": True,
            "segment": {
                "segment": updated_seg.segment,
                "name": updated_seg.name,
                "target_words_min": updated_seg.target_words_min,
                "target_words_max": updated_seg.target_words_max,
                "allowed_angles": updated_seg.allowed_angles,
                "allowed_ctas": updated_seg.allowed_ctas,
                "forbidden_topics": updated_seg.forbidden_topics,
                "register_spec": updated_seg.register_spec,
                "updated_at": updated_seg.updated_at,
            },
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error updating segment playbook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/playbooks/import-markdown")
async def import_playbook_markdown_endpoint(
    request: Request,
    file: Optional[UploadFile] = File(None),
    session: Session = Depends(get_db),
):
    """
    Imports a full playbook markdown file (e.g. segments-catalogues-rulebook-v0.md).
    Validates structure, updates segments & rulebook, and logs audit record.
    """
    content_type = request.headers.get("content-type", "")
    md_content = ""
    operator_name = "operator"

    if file is not None:
        file_bytes = await file.read()
        md_content = file_bytes.decode("utf-8-sig", errors="replace")
    elif "application/json" in content_type:
        payload = await request.json()
        md_content = payload.get("markdown_content", "")
        operator_name = payload.get("operator_name", "operator")
    else:
        form = await request.form()
        form_file = form.get("file")
        operator_name = form.get("operator_name") or operator_name
        if form_file and hasattr(form_file, "read"):
            file_bytes = await form_file.read()
            md_content = file_bytes.decode("utf-8-sig", errors="replace")
        elif "markdown_content" in form:
            md_content = str(form.get("markdown_content"))
        else:
            body_bytes = await request.body()
            md_content = body_bytes.decode("utf-8-sig", errors="replace")

    if not md_content.strip():
        raise HTTPException(status_code=400, detail="Markdown content is empty.")

    try:
        report = import_playbook_markdown(
            session=session,
            md_text=md_content,
            operator_name=operator_name,
        )
        return report
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error importing playbook markdown: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/playbooks/export-markdown")
def export_playbook_markdown_endpoint(session: Session = Depends(get_db)):
    """
    Exports the live PostgreSQL segment playbooks and rulebook as a clean Markdown document.
    """
    md_text = export_playbook_markdown(session)
    return Response(
        content=md_text,
        media_type="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="segments-catalogues-rulebook-active.md"'
        },
    )

