"""
Dashboard API Router (VISION.md §18.3, Stage 7).

Minimal operational dashboard providing real-time read-only SQL aggregates:
- Count of contacts by verification_status
- Count of outbound_queue rows by status (eligible/rendering/sending/sent/held/dead_letter/cancelled)
- Count of vetoes and holds from today (validator_hold, veto_limit)
- Count of suppressions and their reasons (content-free design, enums only, §14.8b)
- Sent count for today vs daily cap (VISION.md §7.4d, cap_enforcer)

NO LLM calls, NO agents. Read-only aggregate SQL only.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.config import settings
from app.pipeline.cap_enforcer import DAILY_TOTAL_SYSTEM_LIMIT
from app.pipeline.reason_formatter import format_hold_reason
from app.pipeline.providers.smtp_provider import SEED_ALLOWLIST
from app.pipeline.sending_controller import (
    get_sending_status,
    set_sending_state,
    set_segment_pause,
    clear_auto_stop,
    get_reconciled_numbers,
)
from app.pipeline.dispatch_worker import dispatch_worker


router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


class SendingToggleRequest(BaseModel):
    active: bool = Field(..., description="True to start sending, False to stop")
    operator_name: str = Field(..., description="Name of operator performing action")


class SegmentPauseRequest(BaseModel):
    segment: str = Field(..., description="Segment identifier")
    paused: bool = Field(..., description="True to pause sending for this segment, False to resume")
    operator_name: str = Field(..., description="Name of operator performing action")


class ClearAutoStopRequest(BaseModel):
    operator_name: str = Field(..., description="Name of operator clearing auto-stop")
    notes: Optional[str] = Field(None, description="Optional notes or rationale")


class VetoesAndHoldsSummary(BaseModel):
    total: int = Field(0, description="Total vetoes and holds recorded today")
    validator_holds: int = Field(0, description="Count of validator holds today")
    veto_limits: int = Field(0, description="Count of veto limit excursions today")
    contacts_held: int = Field(0, description="Count of contacts currently in held status")


class SuppressionSummary(BaseModel):
    total: int = Field(0, description="Total suppressed contacts")
    by_reason: Dict[str, int] = Field(default_factory=dict, description="Counts by suppression reason enum")


class DailySendsSummary(BaseModel):
    sent_today: int = Field(0, description="Total verified emails sent today")
    daily_cap: int = Field(150, description="Calculated daily send allowance ceiling")
    remaining_allowance: int = Field(150, description="Remaining sends permitted today")
    cap_reached: bool = Field(False, description="Whether today's send quota is exhausted")


class ReviewItem(BaseModel):
    id: int = Field(..., description="Entity ID (contact_id or queue_id)")
    type: str = Field(..., description="'contact_reply', 'draft_held', or 'draft_veto'")
    contact_name: str
    company_name: str
    email: str
    reason: str
    plain_reason: Optional[str] = None
    action_hint: Optional[str] = None
    plain_label: Optional[str] = None
    details: Optional[str] = None
    updated_at: Optional[datetime] = None


class DashboardResponse(BaseModel):
    contacts_by_verification_status: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of contacts grouped by verification_status (valid, invalid, etc.)",
    )
    outbound_queue_by_status: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of queue items by status (eligible, rendering, sending, sent, held, dead_letter)",
    )
    vetoes_and_holds_today: VetoesAndHoldsSummary
    suppressions: SuppressionSummary
    daily_sends: DailySendsSummary
    needs_review_items: list[ReviewItem] = Field(
        default_factory=list,
        description="Specific prospects or drafts requiring immediate human attention",
    )
    reconciled_numbers: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Reconciled numbers panel metrics matching database counts exactly",
    )
    sending_control: Optional[Dict[str, Any]] = Field(
        default=None,
        description="System sending state, auto-stop status, and paused segments",
    )
    environment_banner: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Environment banner state (Safe Test Mode vs Live Sending, allowlist count)",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def get_db():
    with get_db_session() as session:
        yield session


@router.get("", response_model=DashboardResponse)
@router.get("/", response_model=DashboardResponse)
def get_dashboard_summary(session: Session = Depends(get_db)) -> DashboardResponse:
    """
    Returns real-time aggregated metrics across contacts, queue, compliance, and delivery.
    """
    start_of_today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. Count of contacts by verification_status
    c_rows = session.execute(
        text("""
            SELECT COALESCE(verification_status, 'unknown') AS v_status, COUNT(*)
              FROM contacts
             GROUP BY verification_status
        """)
    ).fetchall()
    contacts_by_vstatus = {"valid": 0, "invalid": 0, "unknown": 0}
    for row in c_rows:
        contacts_by_vstatus[row[0]] = row[1]

    # 2. Count of outbound_queue rows by status
    q_rows = session.execute(
        text("""
            SELECT status, COUNT(*)
              FROM outbound_queue
             GROUP BY status
        """)
    ).fetchall()
    queue_by_status = {
        "eligible": 0,
        "rendering": 0,
        "sending": 0,
        "sent": 0,
        "held": 0,
        "vetoed": 0,
        "dead_letter": 0,
        "cancelled": 0,
    }
    for row in q_rows:
        queue_by_status[row[0]] = row[1]

    # 3. Count of vetoes and holds from today
    # Holds/vetoes recorded in audit_log
    audit_holds_rows = session.execute(
        text("""
            SELECT reason, COUNT(*)
              FROM audit_log
             WHERE timestamp >= :start_today
               AND reason IN ('validator_hold', 'veto_limit')
             GROUP BY reason
        """),
        {"start_today": start_of_today},
    ).fetchall()

    val_holds = 0
    veto_lims = 0
    for row in audit_holds_rows:
        r_str = str(row[0]).lower()
        if "validator_hold" in r_str:
            val_holds += row[1]
        elif "veto_limit" in r_str:
            veto_lims += row[1]

    # Contacts in held status updated today
    c_held = session.execute(
        text("""
            SELECT COUNT(*)
              FROM contacts
             WHERE status = 'held'
               AND updated_at >= :start_today
        """),
        {"start_today": start_of_today},
    ).scalar() or 0

    vetoes_and_holds = VetoesAndHoldsSummary(
        total=val_holds + veto_lims + c_held,
        validator_holds=val_holds,
        veto_limits=veto_lims,
        contacts_held=c_held,
    )

    # 4. Count of suppressions and reasons (content-free design §14.8b)
    supp_rows = session.execute(
        text("""
            SELECT COALESCE(reason, 'unknown') AS s_reason, COUNT(*)
              FROM suppression
             GROUP BY reason
        """)
    ).fetchall()

    supp_by_reason = {}
    total_suppressions = 0
    for row in supp_rows:
        supp_by_reason[row[0]] = row[1]
        total_suppressions += row[1]

    suppressions = SuppressionSummary(
        total=total_suppressions,
        by_reason=supp_by_reason,
    )

    # 5. Sent count for today vs daily cap (§7.4d, §18.3)
    sent_today_keys = session.execute(
        text("""
            SELECT COUNT(*)
              FROM send_key
             WHERE status = 'sent'
               AND updated_at >= :start_today
        """),
        {"start_today": start_of_today},
    ).scalar() or 0

    sent_today_queue = session.execute(
        text("""
            SELECT COUNT(*)
              FROM outbound_queue
             WHERE status = 'sent'
               AND updated_at >= :start_today
        """),
        {"start_today": start_of_today},
    ).scalar() or 0

    sent_today = max(sent_today_keys, sent_today_queue)

    # Daily cap calculation per §7.4d
    daily_cap = min(settings.target_daily_sends, settings.ramp_target, DAILY_TOTAL_SYSTEM_LIMIT)
    remaining = max(0, daily_cap - sent_today)

    daily_sends = DailySendsSummary(
        sent_today=sent_today,
        daily_cap=daily_cap,
        remaining_allowance=remaining,
        cap_reached=sent_today >= daily_cap,
    )

    # 6. Specific items requiring human review
    review_items: list[ReviewItem] = []

    # Prospects in held status (inbound reply / positive response)
    held_contacts = session.execute(
        text("""
            SELECT id, contact_name, company_name, email, notes, updated_at
              FROM contacts
             WHERE status = 'held'
             ORDER BY updated_at DESC
        """)
    ).fetchall()
    for hc in held_contacts:
        review_items.append(
            ReviewItem(
                id=hc[0],
                type="contact_reply",
                contact_name=hc[1],
                company_name=hc[2],
                email=hc[3],
                reason="Inbound reply received — automation paused for human takeover",
                plain_reason="Inbound reply received — automation paused for human takeover",
                action_hint="Read reply in thread, craft response or mark handled.",
                plain_label="Inbound Reply",
                details=hc[4],
                updated_at=hc[5],
            )
        )

    # Queue items in held status (validator / veto limit)
    held_drafts = session.execute(
        text("""
            SELECT q.id, c.contact_name, c.company_name, c.email, q.rendered_subject, q.attempts, q.updated_at, q.hold_reason, q.failed_validator, q.status, q.attempt_history
              FROM outbound_queue q
              JOIN contacts c ON q.contact_id = c.id
             WHERE q.status IN ('held', 'vetoed')
             ORDER BY q.updated_at DESC
        """)
    ).fetchall()
    for hd in held_drafts:
        q_id, c_name, comp_name, email, subj, att, upd, h_reason, f_val, q_stat, att_hist = hd
        fmt = format_hold_reason(
            hold_reason=h_reason,
            failed_validator=f_val,
            attempts=att or 1,
            status=q_stat,
            attempt_history=att_hist if isinstance(att_hist, list) else [],
        )

        review_items.append(
            ReviewItem(
                id=q_id,
                type="draft_veto" if fmt["is_veto"] else "draft_held",
                contact_name=c_name,
                company_name=comp_name,
                email=email,
                reason=fmt["plain_reason"],
                plain_reason=fmt["plain_reason"],
                action_hint=fmt["action_hint"],
                plain_label=fmt["plain_label"],
                details=subj,
                updated_at=upd,
            )
        )

    sending_status = get_sending_status(session)
    reconciled_nums = get_reconciled_numbers(session)
    env_banner = {
        "test_mode": settings.test_mode,
        "live_email_sending": settings.live_email_sending,
        "allowlist_count": len(SEED_ALLOWLIST),
        "mode_title": "Safe Test Mode" if not settings.live_email_sending else "Live Production Mode",
        "description": (
            f"Outreach restricted to {len(SEED_ALLOWLIST)} verified test allowlist addresses only. Real prospect outreach blocked."
            if not settings.live_email_sending
            else "Live outreach mode is active. Real prospect delivery enabled."
        ),
    }

    return DashboardResponse(
        contacts_by_verification_status=contacts_by_vstatus,
        outbound_queue_by_status=queue_by_status,
        vetoes_and_holds_today=vetoes_and_holds,
        suppressions=suppressions,
        daily_sends=daily_sends,
        needs_review_items=review_items,
        reconciled_numbers=reconciled_nums,
        sending_control=sending_status,
        environment_banner=env_banner,
    )


@router.get("/needs-review", response_model=list[ReviewItem])
def get_needs_review_list(session: Session = Depends(get_db)) -> list[ReviewItem]:
    """Returns only the specific contacts or drafts that require human review."""
    summary = get_dashboard_summary(session=session)
    return summary.needs_review_items


@router.get("/numbers")
def get_numbers_panel(session: Session = Depends(get_db)):
    """Returns reconciled numbers panel metrics per Section D."""
    return get_reconciled_numbers(session)


@router.get("/sending/status")
def get_sending_controller_status(session: Session = Depends(get_db)):
    """Returns sending state, auto-stop condition, and paused segments."""
    return get_sending_status(session)


@router.post("/sending/toggle")
def toggle_sending(request: SendingToggleRequest, session: Session = Depends(get_db)):
    """
    Start / Stop email sending backed by DB flag.
    Workers check before claiming and right before each send.
    """
    result = set_sending_state(
        session=session,
        active=request.active,
        operator_name=request.operator_name,
    )
    if request.active:
        dispatch_worker.wake()
    return {
        "status": "ok",
        "sending_active": result["is_sending_active"],
        "raw_sending_active": result["raw_sending_active"],
        "message": "Sending active" if request.active else "Sending stopped",
        "details": result,
    }


@router.post("/sending/segment-pause")
def pause_segment_sending(request: SegmentPauseRequest, session: Session = Depends(get_db)):
    """Pauses or resumes sending for a specific segment."""
    result = set_segment_pause(
        session=session,
        segment=request.segment,
        paused=request.paused,
        operator_name=request.operator_name,
    )
    return {
        "status": "ok",
        "segment": request.segment,
        "paused": request.paused,
        "details": result,
    }


@router.post("/sending/clear-auto-stop")
def clear_emergency_auto_stop(request: ClearAutoStopRequest, session: Session = Depends(get_db)):
    """Clears emergency auto-stop after deliverability review."""
    result = clear_auto_stop(
        session=session,
        operator_name=request.operator_name,
        notes=request.notes,
    )
    return {
        "status": "ok",
        "message": "Emergency auto-stop cleared",
        "details": result,
    }

