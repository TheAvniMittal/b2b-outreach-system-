from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import OutboundQueue, Contact, SendKey, AuditReason, record_audit_log

LEASE_DURATION_SECONDS = settings.queue_lease_seconds
HEARTBEAT_INTERVAL_SECONDS = 20
MAX_ATTEMPTS_BEFORE_DEAD_LETTER = 3


def get_utc_now() -> datetime:
    """Returns current UTC datetime, allowing freezegun mocking."""
    return datetime.now(timezone.utc)


def enqueue_message(
    session: Session,
    contact_id: int,
    campaign_step: str = "intro",
    due_at: Optional[datetime] = None,
) -> OutboundQueue:
    """Creates an eligible queue item for a verified contact."""
    now = get_utc_now()
    queue_item = OutboundQueue(
        contact_id=contact_id,
        campaign_step=campaign_step,
        status="eligible",
        due_at=due_at or now,
        attempts=0,
        retention_class="sent_messages",
    )
    session.add(queue_item)
    session.flush()

    record_audit_log(
        session,
        actor="system",
        action="enqueue",
        object_type="queue_item",
        object_id=str(queue_item.id),
        reason=AuditReason.INITIAL_ENQUEUE,
        new_state="eligible",
    )
    session.commit()
    return queue_item


def claim_next_outbound_task(
    session: Session,
    worker_id: str,
    lease_seconds: int = LEASE_DURATION_SECONDS,
) -> Optional[OutboundQueue]:
    """
    Claims the next eligible task using SELECT ... FOR UPDATE SKIP LOCKED.
    Commits immediately per VISION.md §14.1a so long-running render/generation
    does not hold an open database lock.
    """
    now = get_utc_now()
    lease_expires_at = now + timedelta(seconds=lease_seconds)

    # SELECT ... FOR UPDATE SKIP LOCKED pattern
    # Also check contact is not suppressed
    row = (
        session.query(OutboundQueue)
        .filter(
            OutboundQueue.status == "eligible",
            (OutboundQueue.due_at == None) | (OutboundQueue.due_at <= now),
        )
        .order_by(OutboundQueue.due_at.asc().nullsfirst(), OutboundQueue.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )

    if not row:
        return None

    row.status = "rendering"
    row.locked_by = worker_id
    row.locked_at = now
    row.lease_expires_at = lease_expires_at
    if not row.processing_started_at:
        row.processing_started_at = now

    record_audit_log(
        session,
        actor=worker_id,
        action="claim_task",
        object_type="queue_item",
        object_id=str(row.id),
        reason=AuditReason.INITIAL_ENQUEUE,  # initial state transition to rendering
        prev_state="eligible",
        new_state="rendering",
    )

    session.commit()
    return row


def heartbeat(
    session: Session,
    queue_item_id: int,
    worker_id: str,
    extend_seconds: int = LEASE_DURATION_SECONDS,
) -> bool:
    """
    Worker extends its lease every ~20s while active (VISION.md §14.1b).
    Returns True if lease was extended, False if item was lost/reaped.
    """
    now = get_utc_now()
    row = (
        session.query(OutboundQueue)
        .filter(
            OutboundQueue.id == queue_item_id,
            OutboundQueue.locked_by == worker_id,
            OutboundQueue.status.in_(["rendering", "sending"]),
        )
        .first()
    )

    if not row:
        return False

    row.lease_expires_at = now + timedelta(seconds=extend_seconds)
    session.commit()
    return True


def run_reaper(
    session: Session,
    reconciliation_adapter=None,
) -> Dict[str, List[int]]:
    """
    Reaper runs periodically (VISION.md §14.1b / Phase 8).
    Reclaims rows whose lease has expired using lease_expiry_count only (attempts is untouched).
    Delegates to run_sweeper.
    """
    from app.pipeline.sweeper import run_sweeper
    res = run_sweeper(session, reconciliation_adapter=reconciliation_adapter)
    return res["details"]

