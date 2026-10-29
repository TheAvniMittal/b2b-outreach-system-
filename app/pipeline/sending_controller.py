"""
Sending Controller & Auto-Stop Enforcer for Narayani Outreach Pipeline (Phase 6).

Implements Section D requirements:
1. Operator Start sending / Stop sending control backed by DB flag.
   - Workers check before claiming AND again right before each send.
   - Stop means no new emails start; one already in progress may finish.
   - Start shows a confirmation with today's number of emails that will be allowed.
   - Optional pause per segment.
2. Auto-stop with a plain reason and red banner if:
   - Hard bounces exceed 5% of sent (after at least 10 sent).
   - Any policy/spam block appears.
   - Any spam complaint arrives.
3. Reconciled Numbers Panel that adds up cleanly with the database.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    SystemControl,
    OutboundQueue,
    Contact,
    SendKey,
    AuditLog,
    Suppression,
    AuditReason,
    record_audit_log,
)
from app.pipeline.cap_enforcer import (
    calculate_daily_cap_allocation,
    DAILY_TOTAL_SYSTEM_LIMIT,
)


KEY_SENDING_ACTIVE = "sending_active"
KEY_PAUSED_SEGMENTS = "paused_segments"
KEY_AUTO_STOP = "auto_stop"


def get_today_utc_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def get_sending_status(session: Session) -> Dict[str, Any]:
    """Retrieves current global sending state, auto-stop condition, and paused segments."""
    # 1. Sending Active
    active_row = session.query(SystemControl).filter_by(key=KEY_SENDING_ACTIVE).first()
    is_active = active_row.value.get("active", True) if active_row and active_row.value else True
    updated_by = active_row.value.get("updated_by", "system") if active_row and active_row.value else "system"
    updated_at = active_row.updated_at if active_row else None

    # 2. Auto-stop Status
    auto_stop_row = session.query(SystemControl).filter_by(key=KEY_AUTO_STOP).first()
    auto_stop_triggered = (
        auto_stop_row.value.get("triggered", False) if auto_stop_row and auto_stop_row.value else False
    )
    auto_stop_reason = (
        auto_stop_row.value.get("reason", None) if auto_stop_row and auto_stop_row.value else None
    )

    # 3. Paused Segments
    seg_row = session.query(SystemControl).filter_by(key=KEY_PAUSED_SEGMENTS).first()
    paused_segments = seg_row.value.get("paused_segments", []) if seg_row and seg_row.value else []

    return {
        "is_sending_active": is_active and not auto_stop_triggered,
        "raw_sending_active": is_active,
        "auto_stop_triggered": auto_stop_triggered,
        "auto_stop_reason": auto_stop_reason,
        "paused_segments": paused_segments,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def is_sending_active(session: Session, segment: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Checks if email sending is permitted right now.
    Workers check before claiming AND again right before each send.
    """
    status = get_sending_status(session)

    # Check auto-stop condition
    if status["auto_stop_triggered"]:
        return False, f"Sending halted: Auto-stop safety triggered ({status['auto_stop_reason']})"

    # Check global active toggle
    if not status["raw_sending_active"]:
        return False, "Sending is currently stopped by operator. No new emails may be sent."

    # Check segment pause if segment provided
    if segment and segment in status["paused_segments"]:
        return False, f"Sending for segment '{segment}' is currently paused by operator."

    return True, None


def set_sending_state(
    session: Session,
    active: bool,
    operator_name: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sets global sending active state with operator attribution and audit log.
    """
    clean_op = operator_name.strip() or "Senior Operator"
    row = session.query(SystemControl).filter_by(key=KEY_SENDING_ACTIVE).first()
    prev_state = "active" if (row and row.value and row.value.get("active", True)) else "stopped"
    new_state = "active" if active else "stopped"

    val = {
        "active": active,
        "updated_by": clean_op,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason or ("Started by operator" if active else "Stopped by operator"),
    }

    if not row:
        row = SystemControl(key=KEY_SENDING_ACTIVE, value=val)
        session.add(row)
    else:
        row.value = val
    session.flush()

    record_audit_log(
        session=session,
        actor=clean_op,
        action="start_sending" if active else "stop_sending",
        object_type="system_controls",
        object_id=KEY_SENDING_ACTIVE,
        reason=AuditReason.SENDING_STARTED if active else AuditReason.SENDING_STOPPED,
        prev_state=prev_state,
        new_state=new_state,
    )
    session.commit()
    return get_sending_status(session)


def set_segment_pause(
    session: Session,
    segment: str,
    paused: bool,
    operator_name: str,
) -> Dict[str, Any]:
    """Pauses or resumes sending for a specific segment."""
    clean_op = operator_name.strip() or "Senior Operator"
    row = session.query(SystemControl).filter_by(key=KEY_PAUSED_SEGMENTS).first()
    current_paused = list(row.value.get("paused_segments", [])) if row and row.value else []

    if paused and segment not in current_paused:
        current_paused.append(segment)
        action_name = "pause_segment"
        audit_res = AuditReason.SEGMENT_PAUSED
        new_s = "paused"
    elif not paused and segment in current_paused:
        current_paused.remove(segment)
        action_name = "resume_segment"
        audit_res = AuditReason.SEGMENT_RESUMED
        new_s = "active"
    else:
        return get_sending_status(session)

    val = {"paused_segments": current_paused, "updated_by": clean_op}
    if not row:
        row = SystemControl(key=KEY_PAUSED_SEGMENTS, value=val)
        session.add(row)
    else:
        row.value = val
    session.flush()

    record_audit_log(
        session=session,
        actor=clean_op,
        action=action_name,
        object_type="segment",
        object_id=segment,
        reason=audit_res,
        prev_state="active" if paused else "paused",
        new_state=new_s,
    )
    session.commit()
    return get_sending_status(session)


def check_auto_stop_conditions(
    session: Session,
    override_sent_today: Optional[int] = None,
    override_hard_bounces: Optional[int] = None,
    override_policy_blocks: Optional[int] = None,
    override_spam_complaints: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Evaluates deliverability metrics for today:
    - Hard bounces > 5% of sent (after at least 10 sent today).
    - Any policy / spam block appears.
    - Any spam complaint arrives.

    If triggered, engages auto-stop immediately and writes an audit log row.
    """
    today_start = get_today_utc_start()

    # 1. Total Sent Today
    if override_sent_today is not None:
        sent_today = override_sent_today
    else:
        sent_today = session.execute(
            text("""
                SELECT COUNT(*)
                  FROM send_key
                 WHERE status = 'sent'
                   AND updated_at >= :today_start
            """),
            {"today_start": today_start},
        ).scalar() or 0

    # 2. Hard Bounces Today (from audit_log or suppression)
    if override_hard_bounces is not None:
        hard_bounces = override_hard_bounces
    else:
        hard_bounces = session.execute(
            text("""
                SELECT COUNT(*)
                  FROM audit_log
                 WHERE reason = 'hard_bounce'
                   AND timestamp >= :today_start
            """),
            {"today_start": today_start},
        ).scalar() or 0

    # 3. Policy / Spam Blocks Today
    if override_policy_blocks is not None:
        policy_blocks = override_policy_blocks
    else:
        policy_blocks = session.execute(
            text("""
                SELECT COUNT(*)
                  FROM audit_log
                 WHERE reason = 'policy_block'
                   AND timestamp >= :today_start
            """),
            {"today_start": today_start},
        ).scalar() or 0

    # 4. Spam Complaints Today
    if override_spam_complaints is not None:
        spam_complaints = override_spam_complaints
    else:
        spam_complaints = session.execute(
            text("""
                SELECT COUNT(*)
                  FROM suppression
                 WHERE reason = 'spam_complaint'
                   AND created_at >= :today_start
            """),
            {"today_start": today_start},
        ).scalar() or 0

    triggered = False
    reason_msg = None

    # Threshold Check 1: Hard Bounce Rate > 5% after min 10 sends
    min_sends = settings.auto_stop_min_sends
    max_rate = settings.auto_stop_bounce_rate_threshold
    if sent_today >= min_sends and sent_today > 0:
        bounce_rate = hard_bounces / sent_today
        if bounce_rate > max_rate:
            triggered = True
            reason_msg = (
                f"Hard bounces exceeded {max_rate:.0%} threshold "
                f"({hard_bounces} bounces out of {sent_today} sent today, {bounce_rate:.1%})"
            )

    # Threshold Check 2: Policy / Spam Block
    if not triggered and policy_blocks > 0:
        triggered = True
        reason_msg = f"Email provider policy/spam block detected ({policy_blocks} block incident today)"

    # Threshold Check 3: Spam Complaint
    if not triggered and spam_complaints > 0:
        triggered = True
        reason_msg = f"Spam complaint received from recipient ({spam_complaints} complaint today)"

    if triggered:
        # Enforce Auto-stop
        row = session.query(SystemControl).filter_by(key=KEY_AUTO_STOP).first()
        val = {
            "triggered": True,
            "reason": reason_msg,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        }
        if not row:
            row = SystemControl(key=KEY_AUTO_STOP, value=val)
            session.add(row)
        else:
            row.value = val
        session.flush()

        # Record audit log
        record_audit_log(
            session=session,
            actor="auto_stop_guard",
            action="trigger_auto_stop",
            object_type="system_controls",
            object_id=KEY_AUTO_STOP,
            reason=AuditReason.AUTO_STOP_TRIGGERED,
            prev_state="active",
            new_state="auto_stopped",
        )
        session.commit()
        return True, reason_msg

    return False, None


def clear_auto_stop(session: Session, operator_name: str, notes: Optional[str] = None) -> Dict[str, Any]:
    """Allows operator to acknowledge and clear auto-stop after reviewing deliverability issues."""
    clean_op = operator_name.strip() or "Senior Operator"
    row = session.query(SystemControl).filter_by(key=KEY_AUTO_STOP).first()
    if row:
        row.value = {"triggered": False, "reason": None, "cleared_by": clean_op, "cleared_at": datetime.now(timezone.utc).isoformat()}
        session.flush()

    record_audit_log(
        session=session,
        actor=clean_op,
        action="clear_auto_stop",
        object_type="system_controls",
        object_id=KEY_AUTO_STOP,
        reason=AuditReason.AUTO_STOP_CLEARED,
        prev_state="auto_stopped",
        new_state="cleared",
    )
    session.commit()
    return get_sending_status(session)


def get_reconciled_numbers(session: Session) -> Dict[str, Any]:
    """
    Computes exact, reconciled numbers panel metrics per Section D:
    1. today's allowed number (from the cap enforcer)
    2. sent today
    3. remaining today
    4. waiting in queue
    5. awaiting my approval
    6. held for review
    7. skipped (veto)
    8. replies waiting
    """
    today_start = get_today_utc_start()

    # 1. Sent today (from send_key where status='sent' or queue where status='sent' today)
    sent_today = session.execute(
        text("""
            SELECT COUNT(*)
              FROM send_key
             WHERE status = 'sent'
               AND updated_at >= :today_start
        """),
        {"today_start": today_start},
    ).scalar() or 0

    # 2. Today's Allowed (from cap enforcer)
    allocation = calculate_daily_cap_allocation(
        followups_due=0,
        new_due=100,
        target=settings.target_daily_sends,
        ramp_target=settings.ramp_target,
    )
    today_allowed = allocation.budget

    # 3. Remaining today
    remaining_today = max(0, today_allowed - sent_today)

    # 4. Waiting in queue (status == 'eligible')
    waiting_in_queue = session.execute(
        text("SELECT COUNT(*) FROM outbound_queue WHERE status = 'eligible'")
    ).scalar() or 0

    # 5. Awaiting my approval (drafts in 'held' status in outbound_queue)
    awaiting_approval = session.execute(
        text("SELECT COUNT(*) FROM outbound_queue WHERE status = 'held'")
    ).scalar() or 0

    # 6. Replies waiting (contacts in 'held' status awaiting sales takeover)
    replies_waiting = session.execute(
        text("SELECT COUNT(*) FROM contacts WHERE status = 'held'")
    ).scalar() or 0

    # 7. Held for review (adds up to held drafts + inbound contact replies)
    held_for_review = awaiting_approval + replies_waiting

    # 8. Skipped (veto) (queue items in 'vetoed' status)
    skipped_veto = session.execute(
        text("SELECT COUNT(*) FROM outbound_queue WHERE status = 'vetoed'")
    ).scalar() or 0

    # Active / Rendering / In progress
    in_progress = session.execute(
        text("SELECT COUNT(*) FROM outbound_queue WHERE status IN ('rendering', 'sending')")
    ).scalar() or 0

    return {
        "today_allowed": today_allowed,
        "sent_today": sent_today,
        "remaining_today": remaining_today,
        "waiting_in_queue": waiting_in_queue,
        "awaiting_approval": awaiting_approval,
        "replies_waiting": replies_waiting,
        "held_for_review": held_for_review,
        "skipped_veto": skipped_veto,
        "in_progress": in_progress,
        "reconciled": (held_for_review == awaiting_approval + replies_waiting) and (remaining_today == max(0, today_allowed - sent_today)),
    }
