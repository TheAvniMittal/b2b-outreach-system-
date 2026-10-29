import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from app.db.models import Contact, OutboundQueue, Suppression, AuditReason, record_audit_log
from app.schemas.a3_contract import A3ReplyContract, A3Kind, A3Intent, A3SuppressionSignal


def hash_email(email: str) -> str:
    """Computes one-way SHA-256 hash of normalized email (VISION.md §14.8b)."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def apply_mechanical_overrides(
    session: Session,
    contact: Contact,
    contract: A3ReplyContract,
) -> Tuple[A3ReplyContract, dict]:
    """
    Applies deterministic mechanical overrides in CODE, not the prompt (VISION.md §7.5):
    1. If suppression signal is present: suppress contact regardless of anything else.
    2. If confidence < 0.6: force kind='unknown' and route to human review.
    3. If kind == 'auto_reply': snooze sequence due_at by 5 days (keep active).
    4. If kind == 'human':
       - If not_interested: cancel pending sequence items, mark contacted.
       - If positive/ambiguous: set contact to held for human sales takeover.
    5. Un-suppression is NOT reachable from any model output — human-only, full stop.
    """
    actions_taken = {
        "suppressed": False,
        "automation_paused": False,
        "routed_to_human": False,
        "snoozed_auto_reply": False,
        "declined": False,
        "original_kind": contract.kind,
        "final_kind": contract.kind,
    }

    # 1. SUPPRESSION OVERRIDE (Inviolable Gate, §7.5, §7.5a)
    has_suppression_signal = (
        contract.suppression_signal not in [A3SuppressionSignal.NONE, A3SuppressionSignal.UNKNOWN]
        or contract.intent == A3Intent.UNSUBSCRIBE_REQUEST
    )

    if has_suppression_signal:
        email_hash = hash_email(contact.email)
        existing_suppression = session.query(Suppression).filter_by(email_hash=email_hash).first()
        if not existing_suppression:
            suppression_entry = Suppression(
                email_hash=email_hash,
                contact_id=contact.id,
                reason=contract.suppression_signal.value if contract.suppression_signal != A3SuppressionSignal.NONE else "unsubscribe_request",
                created_by="a3_reply_classifier",
                retention_class="permanent",
            )
            session.add(suppression_entry)

        prev_status = contact.status
        contact.status = "suppressed"

        # Cancel in-flight outbound queue items for this contact (§7.5a)
        session.query(OutboundQueue).filter(
            OutboundQueue.contact_id == contact.id,
            OutboundQueue.status.in_(["eligible", "rendering", "pending", "held"]),
        ).update(
            {"status": "cancelled", "locked_by": None},
            synchronize_session=False,
        )

        record_audit_log(
            session,
            actor="a3_reply_classifier",
            action="suppress",
            object_type="contact",
            object_id=str(contact.id),
            reason=AuditReason.UNSUBSCRIBE_REQUEST,
            prev_state=prev_status,
            new_state="suppressed",
        )
        actions_taken["suppressed"] = True

    # 2. CONFIDENCE FLOOR OVERRIDE (§7.5)
    # If confidence < 0.6: force kind="unknown" and route to human review
    elif contract.confidence < 0.6:
        contract = contract.model_copy(update={"kind": A3Kind.UNKNOWN})
        actions_taken["final_kind"] = A3Kind.UNKNOWN

        if contact.status != "suppressed":
            prev_status = contact.status
            contact.status = "held"
            summary_note = contract.summary or "Ambiguous or low confidence reply"
            contact.notes = f"{contact.notes or ''}\n[A3 Low Confidence ({contract.confidence:.2f})]: {summary_note}".strip()
            record_audit_log(
                session,
                actor="a3_reply_classifier",
                action="route_to_human",
                object_type="contact",
                object_id=str(contact.id),
                reason=AuditReason.VALIDATOR_HOLD,
                prev_state=prev_status,
                new_state="held",
            )
            actions_taken["routed_to_human"] = True

    # 3. AUTO-REPLY / OUT-OF-OFFICE OVERRIDE
    elif contract.kind == A3Kind.AUTO_REPLY and contact.status != "suppressed":
        snooze_until = datetime.now(timezone.utc) + timedelta(days=5)
        session.query(OutboundQueue).filter(
            OutboundQueue.contact_id == contact.id,
            OutboundQueue.status.in_(["eligible", "pending"]),
        ).update(
            {"due_at": snooze_until},
            synchronize_session=False,
        )
        record_audit_log(
            session,
            actor="a3_reply_classifier",
            action="snooze_auto_reply",
            object_type="contact",
            object_id=str(contact.id),
            reason=AuditReason.OUT_OF_OFFICE_SNOOZE,
            prev_state=contact.status,
            new_state=contact.status,
        )
        actions_taken["snoozed_auto_reply"] = True

    # 4. HUMAN REPLY HANDLING (§7.5)
    elif contract.kind == A3Kind.HUMAN and contact.status != "suppressed":
        if contract.intent == A3Intent.NOT_INTERESTED:
            # Declined: cancel sequence and mark contacted
            prev_status = contact.status
            contact.status = "contacted"
            session.query(OutboundQueue).filter(
                OutboundQueue.contact_id == contact.id,
                OutboundQueue.status.in_(["eligible", "pending", "rendering"]),
            ).update(
                {"status": "cancelled", "locked_by": None},
                synchronize_session=False,
            )
            summary_note = contract.summary or "Prospect declined offer"
            contact.notes = f"{contact.notes or ''}\n[A3 Declined]: {summary_note}".strip()
            record_audit_log(
                session,
                actor="a3_reply_classifier",
                action="decline_received",
                object_type="contact",
                object_id=str(contact.id),
                reason=AuditReason.HUMAN_REPLY,
                prev_state=prev_status,
                new_state="contacted",
            )
            actions_taken["declined"] = True
        else:
            # Positive or actionable reply: Pause automation for human sales takeover
            prev_status = contact.status
            contact.status = "held"
            session.query(OutboundQueue).filter(
                OutboundQueue.contact_id == contact.id,
                OutboundQueue.status.in_(["eligible", "pending"]),
            ).update(
                {"status": "held", "locked_by": None},
                synchronize_session=False,
            )
            summary_note = contract.summary or "Inbound reply received"
            contact.notes = f"{contact.notes or ''}\n[Inbound Reply - {contract.intent.value}]: {summary_note}".strip()
            record_audit_log(
                session,
                actor="a3_reply_classifier",
                action="pause_automation",
                object_type="contact",
                object_id=str(contact.id),
                reason=AuditReason.HUMAN_REPLY,
                prev_state=prev_status,
                new_state="held",
            )
            actions_taken["automation_paused"] = True

    session.commit()
    return contract, actions_taken
