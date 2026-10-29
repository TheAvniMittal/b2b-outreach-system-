import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db_session
from app.db.models import OutboundQueue, SendKey, AuditReason, SystemControl, record_audit_log

logger = logging.getLogger("sweeper")

SWEEPER_HEALTH_KEY = "sweeper_health"


def run_sweeper(
    session: Session,
    reconciliation_adapter=None,
    now_override: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Periodic recovery sweeper (VISION.md §14.1b / Phase 8):
    1. Runs every 60s and at startup before worker claims.
    2. Decides expiry strictly using database clock (func.now()), not application clock.
       Supports now_override or freezegun for simulation testing.
    3. Concurrency: SELECT ... FOR UPDATE SKIP LOCKED in batches of 50.
    4. Exactly one audit row per recovered item; zero audit rows for empty sweeps.
    5. Reclaims rendering items: returns to 'eligible', increments lease_expiry_count (NOT attempts).
       3 lease expiries -> 'dead_letter'.
    6. Reconciles sending items: checks send_key, never requeues blindly.
       Ambiguous cases become 'held' with reason:
       "We can't tell whether this email was sent. Check your Sent folder."
    7. Hard maximum processing time: items in 'rendering' or 'sending' exceeding
       max_processing_seconds (default 15m) are reclaimed even if heartbeats continue.
    8. Records last_sweep_at and recovered_count in system_controls table.
    """
    if now_override is not None:
        db_now = now_override
        if db_now.tzinfo is None:
            db_now = db_now.replace(tzinfo=timezone.utc)
    elif type(datetime.now()).__module__ == "freezegun.api":
        db_now = datetime.now(timezone.utc)
    else:
        db_now = session.query(func.now()).scalar()
        if not db_now:
            db_now = datetime.now(timezone.utc)
        elif db_now.tzinfo is None:
            db_now = db_now.replace(tzinfo=timezone.utc)

    max_proc_delta = timedelta(seconds=settings.max_processing_seconds)
    max_proc_cutoff = db_now - max_proc_delta

    # Fetch expired or hung rows using FOR UPDATE SKIP LOCKED in batches of 50
    # Expired: lease_expires_at < db_now
    # Hung: processing_started_at < max_proc_cutoff
    expired_query = (
        session.query(OutboundQueue)
        .filter(
            OutboundQueue.status.in_(["rendering", "sending"]),
            or_(
                (OutboundQueue.lease_expires_at != None) & (OutboundQueue.lease_expires_at < db_now),
                (OutboundQueue.processing_started_at != None) & (OutboundQueue.processing_started_at < max_proc_cutoff),
            ),
        )
        .order_by(OutboundQueue.id.asc())
        .with_for_update(skip_locked=True)
        .limit(50)
    )

    rows = expired_query.all()

    results: Dict[str, List[int]] = {
        "reclaimed": [],
        "dead_letter": [],
        "held": [],
        "marked_sent": [],
    }

    for row in rows:
        prev_status = row.status
        is_hung = bool(
            row.processing_started_at
            and (row.processing_started_at < max_proc_cutoff)
        )

        # Count one lease expiry (crash/timeout) - NEVER increment draft attempts!
        row.lease_expiry_count = (row.lease_expiry_count or 0) + 1

        # 3 lease expiries -> dead_letter
        if row.lease_expiry_count >= 3:
            row.status = "dead_letter"
            row.locked_by = None
            row.locked_at = None
            row.lease_expires_at = None
            row.processing_started_at = None
            row.hold_reason = "Technical failure: processing failed 3 times (crashes or timeouts)"

            record_audit_log(
                session=session,
                actor="system:sweeper",
                action="reaper_dead_letter",
                object_type="outbound_queue",
                object_id=str(row.id),
                reason=AuditReason.DEAD_LETTER,
                prev_state=prev_status,
                new_state="dead_letter",
            )
            results["dead_letter"].append(row.id)
            continue

        if prev_status == "rendering":
            # Rendering items: safe to reclaim, return to eligible
            row.status = "eligible"
            row.locked_by = None
            row.locked_at = None
            row.lease_expires_at = None
            row.processing_started_at = None

            audit_reason = AuditReason.WORKER_CRASH if is_hung else AuditReason.LEASE_EXPIRED
            record_audit_log(
                session=session,
                actor="system:sweeper",
                action="reaper_reclaim",
                object_type="outbound_queue",
                object_id=str(row.id),
                reason=audit_reason,
                prev_state="rendering",
                new_state="eligible",
            )
            results["reclaimed"].append(row.id)

        elif prev_status == "sending":
            # Sending items: dangerous window (§14.1c). Reconcile using send_key.
            send_key_rec = (
                session.query(SendKey)
                .filter(SendKey.queue_item_id == row.id)
                .order_by(SendKey.id.desc())
                .first()
            )

            # Check if reconciliation adapter provided
            rec_result = None
            if reconciliation_adapter and hasattr(reconciliation_adapter, "reconcile"):
                try:
                    rec_result = reconciliation_adapter.reconcile(session, row)
                except Exception as e:
                    logger.error(f"Reconciliation adapter error for item {row.id}: {e}")
                    rec_result = "unknown"

            if rec_result == "sent" or (send_key_rec and send_key_rec.status == "sent"):
                # Confirmed sent
                row.status = "sent"
                row.locked_by = None
                row.locked_at = None
                row.lease_expires_at = None
                row.processing_started_at = None

                record_audit_log(
                    session=session,
                    actor="system:sweeper",
                    action="sweeper_reconcile_sent",
                    object_type="outbound_queue",
                    object_id=str(row.id),
                    reason=AuditReason.RECONCILE_FOUND,
                    prev_state="sending",
                    new_state="sent",
                )
                results["marked_sent"].append(row.id)

            elif rec_result == "not_sent":
                # Confirmed not sent
                row.status = "eligible"
                row.locked_by = None
                row.locked_at = None
                row.lease_expires_at = None
                row.processing_started_at = None

                record_audit_log(
                    session=session,
                    actor="system:sweeper",
                    action="sweeper_reconcile_not_sent",
                    object_type="outbound_queue",
                    object_id=str(row.id),
                    reason=AuditReason.RECONCILE_NOT_FOUND,
                    prev_state="sending",
                    new_state="eligible",
                )
                results["reclaimed"].append(row.id)

            else:
                # Ambiguous: cannot verify provider delivery (e.g. SMTP crash or no provider response)
                # Hold the item with required plain explanation
                plain_msg = "We can't tell whether this email was sent. Check your Sent folder."
                row.status = "held"
                row.hold_reason = plain_msg
                row.failed_validator = "reconcile_ambiguous"
                row.locked_by = None
                row.locked_at = None
                row.lease_expires_at = None
                row.processing_started_at = None

                record_audit_log(
                    session=session,
                    actor="system:sweeper",
                    action="sweeper_reconcile_ambiguous_hold",
                    object_type="outbound_queue",
                    object_id=str(row.id),
                    reason=AuditReason.RECONCILE_AMBIGUOUS_HOLD,
                    prev_state="sending",
                    new_state="held",
                )
                results["held"].append(row.id)

    total_recovered = (
        len(results["reclaimed"])
        + len(results["dead_letter"])
        + len(results["held"])
        + len(results["marked_sent"])
    )

    # Update sweeper health in SystemControl
    ctrl = session.query(SystemControl).filter(SystemControl.key == SWEEPER_HEALTH_KEY).first()
    iso_now = db_now.isoformat()
    health_payload = {
        "last_sweep_at": iso_now,
        "recovered_in_latest_sweep": total_recovered,
        "reclaimed": len(results["reclaimed"]),
        "dead_letter": len(results["dead_letter"]),
        "held": len(results["held"]),
        "marked_sent": len(results["marked_sent"]),
        "interval_seconds": settings.sweeper_interval_seconds,
    }

    if not ctrl:
        ctrl = SystemControl(key=SWEEPER_HEALTH_KEY, value=health_payload)
        session.add(ctrl)
    else:
        ctrl.value = health_payload

    session.commit()

    if total_recovered > 0:
        logger.info(
            f"Recovery sweeper finished: {total_recovered} items recovered "
            f"({len(results['reclaimed'])} reclaimed, {len(results['held'])} held, "
            f"{len(results['dead_letter'])} dead_letter, {len(results['marked_sent'])} marked sent)."
        )

    return {
        "recovered_count": total_recovered,
        "last_sweep_at": iso_now,
        "details": results,
    }


def get_sweeper_health(session: Session) -> Dict[str, Any]:
    """
    Returns sweeper health status:
    - last_sweep_at
    - recovered_in_latest_sweep
    - is_stale: True if last sweep is older than 3 intervals (default 180s)
    - status_color: 'green' or 'red'
    - message: Plain language explanation
    """
    ctrl = session.query(SystemControl).filter(SystemControl.key == SWEEPER_HEALTH_KEY).first()
    if not ctrl or not ctrl.value or not ctrl.value.get("last_sweep_at"):
        return {
            "status": "warning",
            "status_color": "yellow",
            "last_sweep_at": None,
            "recovered_in_latest_sweep": 0,
            "is_stale": True,
            "message": "Sweeper has not run yet.",
        }

    val = ctrl.value
    last_sweep_str = val.get("last_sweep_at")
    recovered = val.get("recovered_in_latest_sweep", 0)
    interval = val.get("interval_seconds", settings.sweeper_interval_seconds)

    try:
        last_dt = datetime.fromisoformat(last_sweep_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        elapsed_sec = (now_dt - last_dt).total_seconds()
    except Exception:
        elapsed_sec = 9999.0

    stale_threshold = 3 * interval
    is_stale = elapsed_sec > stale_threshold

    if is_stale:
        return {
            "status": "stale",
            "status_color": "red",
            "last_sweep_at": last_sweep_str,
            "elapsed_seconds": int(elapsed_sec),
            "recovered_in_latest_sweep": recovered,
            "is_stale": True,
            "message": f"Recovery check is overdue (last run {int(elapsed_sec)}s ago, expected within {stale_threshold}s).",
        }

    return {
        "status": "healthy",
        "status_color": "green",
        "last_sweep_at": last_sweep_str,
        "elapsed_seconds": int(elapsed_sec),
        "recovered_in_latest_sweep": recovered,
        "is_stale": False,
        "message": f"Recovery check active. {recovered} items recovered in latest sweep.",
    }


# Background sweeper daemon thread manager
class SweeperDaemon:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="RecoverySweeperDaemon")
        self._thread.start()
        logger.info("Recovery sweeper daemon started.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Recovery sweeper daemon stopped.")

    def _run_loop(self):
        # Run startup sweep immediately
        try:
            with get_db_session() as db:
                run_sweeper(db)
        except Exception as e:
            logger.error(f"Startup recovery sweep failed: {e}")

        while not self._stop_event.is_set():
            # Wait for interval or stop event
            if self._stop_event.wait(timeout=settings.sweeper_interval_seconds):
                break
            try:
                with get_db_session() as db:
                    run_sweeper(db)
            except Exception as e:
                logger.error(f"Periodic recovery sweep error: {e}")


sweeper_daemon = SweeperDaemon()
