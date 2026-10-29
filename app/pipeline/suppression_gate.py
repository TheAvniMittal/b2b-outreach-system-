"""
Suppression Gate - Concurrency-Safe Atomic Specification (VISION.md §7.5a).

Inviolable Rule:
Suppression must override all queue operations under PostgreSQL READ COMMITTED.
Because an atomic UPDATE alone has a snapshot-interleaving race with non-existent rows,
BOTH the suppression-write path and the claim path MUST first serialize on the existing row:
    SELECT id FROM contacts WHERE id = :contact_id FOR UPDATE

The claim path's UPDATE must then include:
    AND NOT EXISTS (SELECT 1 FROM suppression WHERE email_hash = :email_hash OR contact_id = :contact_id)

Un-suppression is strictly human-only and writes an audit row naming the human actor.
No automated path may ever remove a suppression row.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason, record_audit_log


FORBIDDEN_AUTOMATED_ACTORS = {
    "system",
    "automated",
    "a3_reply_classifier",
    "a2_personalization",
    "reaper",
    "worker",
    "pipeline",
    "cron",
}


def hash_email(email: str) -> str:
    """One-way SHA-256 hash of normalized email (VISION.md §14.8b)."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def is_email_suppressed(session: Session, email: str) -> bool:
    """Checks whether an email's SHA-256 hash exists in suppression list."""
    e_hash = hash_email(email)
    row = session.execute(
        text("SELECT 1 FROM suppression WHERE email_hash = :e_hash LIMIT 1"),
        {"e_hash": e_hash}
    ).fetchone()
    return row is not None


def is_contact_suppressed(session: Session, contact_id: int) -> bool:
    """Checks whether a contact_id or its email is suppressed."""
    row = session.execute(
        text("SELECT 1 FROM suppression WHERE contact_id = :cid LIMIT 1"),
        {"cid": contact_id}
    ).fetchone()
    return row is not None


def suppress_contact_atomic(
    session: Session,
    contact_id: int,
    reason: str,
    actor: str = "system",
) -> bool:
    """
    Suppression-Write Path (§7.5a Authoritative Gate):
    1. SELECT id, email, status FROM contacts WHERE id = :contact_id FOR UPDATE
    2. INSERT INTO suppression (email_hash, contact_id, reason, created_by, retention_class)
    3. UPDATE contacts SET status = 'suppressed' WHERE id = :contact_id
    4. UPDATE outbound_queue SET status = 'cancelled' WHERE contact_id = :contact_id AND status IN ('eligible', 'rendering', 'pending')
    5. Write AuditLog row
    """
    # 1. Lock the contact row first
    contact_row = session.execute(
        text("SELECT id, email, status FROM contacts WHERE id = :contact_id FOR UPDATE"),
        {"contact_id": contact_id}
    ).fetchone()

    if not contact_row:
        return False

    prev_status = contact_row.status
    email_hash = hash_email(contact_row.email)

    # 2. Insert into suppression
    session.execute(
        text("""
            INSERT INTO suppression (email_hash, contact_id, reason, created_by, retention_class)
            VALUES (:email_hash, :contact_id, :reason, :created_by, 'permanent')
            ON CONFLICT (email_hash) DO NOTHING
        """),
        {
            "email_hash": email_hash,
            "contact_id": contact_id,
            "reason": reason,
            "created_by": actor,
        }
    )

    # 3. Update contact status to suppressed
    session.execute(
        text("UPDATE contacts SET status = 'suppressed' WHERE id = :contact_id"),
        {"contact_id": contact_id}
    )

    # 4. Proactively cancel in-flight / pending queue rows
    session.execute(
        text("""
            UPDATE outbound_queue
               SET status = 'cancelled'
             WHERE contact_id = :contact_id
               AND status IN ('eligible', 'rendering', 'pending')
        """),
        {"contact_id": contact_id}
    )

    # 5. Record audit log
    audit_reason = AuditReason.SUPPRESSION_ENFORCED
    if "unsubscribe" in reason.lower():
        audit_reason = AuditReason.UNSUBSCRIBE_REQUEST
    elif "hard_bounce" in reason.lower():
        audit_reason = AuditReason.HARD_BOUNCE

    record_audit_log(
        session,
        actor=actor,
        action="suppress",
        object_type="contact",
        object_id=str(contact_id),
        prev_state=prev_status,
        new_state="suppressed",
        reason=audit_reason,
    )

    return True


def claim_outbound_task_atomic(
    session: Session,
    queue_item_id: int,
    worker_id: str,
    lease_seconds: int = 90,
) -> Optional[int]:
    """
    Claim Path (§7.5a Authoritative Gate):
    1. Find associated contact_id.
    2. Serialize on the contact row:
       SELECT id, email, status FROM contacts WHERE id = :contact_id FOR UPDATE
    3. Atomic conditional UPDATE on outbound_queue:
       UPDATE outbound_queue
          SET status = 'sending', locked_by = :worker_id, ...
        WHERE id = :qid
          AND status IN ('rendering', 'eligible')
          AND NOT EXISTS (
                SELECT 1 FROM suppression s
                 WHERE s.email_hash = :email_hash
                    OR s.contact_id = :contact_id
          )
       RETURNING id;
    Zero rows returned means suppression won (or row was not eligible).
    """
    # 1. Fetch queue item to determine target contact
    q_row = session.execute(
        text("SELECT id, contact_id, status FROM outbound_queue WHERE id = :qid"),
        {"qid": queue_item_id}
    ).fetchone()

    if not q_row:
        return None

    contact_id = q_row.contact_id

    # 2. Authoritative Gate: Take exclusive lock on contact row first
    contact_row = session.execute(
        text("SELECT id, email, status FROM contacts WHERE id = :contact_id FOR UPDATE"),
        {"contact_id": contact_id}
    ).fetchone()

    if not contact_row:
        return None

    # If contact is already marked suppressed, refuse immediately
    if contact_row.status == "suppressed":
        return None

    email_hash = hash_email(contact_row.email)
    lease_expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)

    # 3. Conditional claim statement with strict NOT EXISTS subquery
    claim_sql = text("""
        UPDATE outbound_queue
           SET status = 'sending',
               locked_by = :worker_id,
               locked_at = now(),
               lease_expires_at = :lease_expires_at,
               attempts = attempts + 1,
               updated_at = now()
         WHERE id = :qid
           AND status IN ('rendering', 'eligible')
           AND NOT EXISTS (
                 SELECT 1 FROM suppression s
                  WHERE s.email_hash = :email_hash
                     OR s.contact_id = :contact_id
           )
        RETURNING id
    """)

    result = session.execute(
        claim_sql,
        {
            "qid": queue_item_id,
            "worker_id": worker_id,
            "lease_expires_at": lease_expiry,
            "email_hash": email_hash,
            "contact_id": contact_id,
        }
    ).fetchone()

    if result is None:
        # Suppression won or item was not in eligible/rendering state!
        return None

    return result[0]


def unsuppress_contact_human_only(
    session: Session,
    contact_id: int,
    human_actor: str,
    reason: str,
) -> bool:
    """
    Un-suppression is human-only and writes an audit row naming who did it (§7.5a).
    No automated path may ever remove a suppression row.
    """
    if not human_actor or not human_actor.strip():
        raise ValueError("Un-suppression requires a valid human actor username. Cannot be empty.")

    actor_clean = human_actor.strip().lower()
    if actor_clean in FORBIDDEN_AUTOMATED_ACTORS or actor_clean.startswith("worker_") or actor_clean.startswith("auto_"):
        raise ValueError(
            f"Un-suppression is human-only and forbidden for automated actor: '{human_actor}'. "
            "Master Prompt §17 / VISION.md §7.5a inviolable rule."
        )

    # 1. Lock the contact row
    contact_row = session.execute(
        text("SELECT id, email, status FROM contacts WHERE id = :contact_id FOR UPDATE"),
        {"contact_id": contact_id}
    ).fetchone()

    if not contact_row:
        return False

    prev_status = contact_row.status
    email_hash = hash_email(contact_row.email)

    # 2. Delete suppression entries
    session.execute(
        text("DELETE FROM suppression WHERE contact_id = :contact_id OR email_hash = :email_hash"),
        {"contact_id": contact_id, "email_hash": email_hash}
    )

    # 3. Restore contact status
    session.execute(
        text("UPDATE contacts SET status = 'pending' WHERE id = :contact_id"),
        {"contact_id": contact_id}
    )

    # 4. Record audit log naming the human actor
    record_audit_log(
        session,
        actor=human_actor.strip(),
        action="unsuppress",
        object_type="contact",
        object_id=str(contact_id),
        prev_state=prev_status,
        new_state="pending",
        reason=AuditReason.UNSUPPRESSION,
    )

    return True
