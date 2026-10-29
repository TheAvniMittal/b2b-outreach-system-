import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from app.config import settings
from app.core.groq_provider import limiter, circuit_breaker, GroqProviderError
from app.db.models import OutboundQueue, Contact, AuditReason, record_audit_log
from app.pipeline.queue import claim_next_outbound_task, heartbeat, get_utc_now
from app.pipeline.providers.base import EmailProvider
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.pipeline.providers.smtp_provider import SMTPEmailProvider
from app.pipeline.sender import execute_send
from app.pipeline.sending_controller import is_sending_active, check_auto_stop_conditions
from app.pipeline.catalogue_service import resolve_send_attachment, validate_attachment_text_alignment
from app.workers.a2_personalization.service import run_a2_for_contact

logger = logging.getLogger("pipeline_runner")


def run_single_pipeline_item(
    session: Session,
    worker_id: str,
    provider: EmailProvider,
) -> Optional[Dict[str, Any]]:
    """
    Executes a single queue item through the entire pipeline (Phase 8):
    1. Check Auto-stop safety conditions (bounce rate, policy block, spam complaints).
    2. Check sending toggle (active/stopped).
    3. Check shared Groq circuit breaker (key rejected, daily limit, transient backoff).
    4. Check daily AI token budget via Groq limiter.
    5. Claim next task (FOR UPDATE SKIP LOCKED + Lease).
    6. Heartbeat renewed from work loop between steps.
    7. A2 Personalization & Validators with round tracking and format re-ask.
       - Provider errors handled outside draft loop, never count as attempts, never reach validators.
       - 3 draft attempts or 2 consecutive identical failures -> held.
       - 3 vetoes -> held for review; otherwise decay delay.
    8. If emitted: pre-send check, heartbeat, pre-commit send_key ('sending') -> call provider -> mark 'sent'.
    """
    # 0. Check Auto-stop safety conditions (bounce rate, policy block, spam complaints)
    auto_stop_triggered, auto_stop_msg = check_auto_stop_conditions(session)
    if auto_stop_triggered:
        logger.warning(f"Pipeline sending halted by auto-stop: {auto_stop_msg}")
        return {"status": "auto_stop_halted", "reason": auto_stop_msg}

    # 1. Sending Active check before claiming (§Section D)
    sending_active, stop_reason = is_sending_active(session)
    if not sending_active:
        logger.info(f"Pipeline sending halted: {stop_reason}")
        return {"status": "sending_stopped", "reason": stop_reason}

    # 2. Groq circuit breaker check
    cb_available, cb_msg = circuit_breaker.is_available()
    if not cb_available:
        logger.warning(f"Pipeline sending paused by AI circuit breaker: {cb_msg}")
        return {"status": "ai_service_unavailable", "reason": cb_msg}

    # 3. AI token budget check
    can_send, budget_msg = limiter.check_can_send(session, settings.a2_model)
    if not can_send:
        logger.warning(f"Pipeline sending paused: {budget_msg}")
        return {"status": "paused_daily_tokens_exhausted", "reason": budget_msg}

    # 4. Claim next task
    queue_item = claim_next_outbound_task(session, worker_id=worker_id)
    if not queue_item:
        logger.info("No eligible queue items available.")
        return None

    contact = session.query(Contact).filter(Contact.id == queue_item.contact_id).first()
    if not contact:
        logger.error(f"Contact {queue_item.contact_id} not found.")
        return None

    # Check segment pause
    seg_active, seg_reason = is_sending_active(session, segment=contact.segment)
    if not seg_active:
        # Revert claimed task to eligible
        queue_item.status = "eligible"
        queue_item.locked_by = None
        queue_item.locked_at = None
        queue_item.lease_expires_at = None
        queue_item.processing_started_at = None
        session.commit()
        logger.info(f"Skipping task {queue_item.id}: {seg_reason}")
        return {"status": "segment_paused", "segment": contact.segment, "reason": seg_reason}

    logger.info(f"Processing queue item {queue_item.id} for contact {contact.id} ({contact.email})...")

    # Heartbeat from work loop before starting draft generation
    heartbeat(session, queue_item.id, worker_id=worker_id)

    # Calculate current round number from existing attempt_history
    existing_history = list(queue_item.attempt_history or [])
    highest_round = max([h.get("round", 1) for h in existing_history], default=0)
    if highest_round == 0:
        current_round = 1
    elif queue_item.attempts == 0 and existing_history:
        current_round = highest_round + 1
    else:
        current_round = highest_round

    # 5. Run A2 Personalization with provider error handling outside draft loop
    try:
        a2_state = run_a2_for_contact(
            contact_id=contact.id,
            db=session,
            round_number=current_round,
            existing_history=existing_history,
            include_notes=True,
            include_pii=False,
        )
        circuit_breaker.record_success()
    except GroqProviderError as prov_err:
        # Provider error: NEVER increment attempts, NEVER reach validators
        orig_err = prov_err.original_error or prov_err
        err_type, plain_msg = circuit_breaker.record_failure(orig_err, retry_after=prov_err.retry_after)
        queue_item.provider_retry_count = (queue_item.provider_retry_count or 0) + 1

        now = get_utc_now()

        # Key rejected (401/403)
        if err_type == "auth_rejected":
            queue_item.status = "eligible"
            queue_item.hold_reason = "Groq rejected the key. Update it in Connections."
            queue_item.locked_by = None
            queue_item.locked_at = None
            queue_item.lease_expires_at = None
            queue_item.processing_started_at = None
            session.commit()
            return {"status": "auth_rejected", "reason": plain_msg}

        # Check provider retry cap & long-term timeout
        # Only hold as 'provider_unavailable' if it has failed multiple times (>= max_retries)
        if queue_item.provider_retry_count >= settings.provider_max_retries:
            item_processing_age_hours = (
                (now - queue_item.processing_started_at).total_seconds() / 3600.0
                if queue_item.processing_started_at
                else 0.0
            )
            if item_processing_age_hours >= settings.provider_unavailable_timeout_hours or queue_item.provider_retry_count >= settings.provider_max_retries * 2:
                queue_item.status = "held"
                queue_item.hold_reason = "The AI service was unavailable"
                queue_item.failed_validator = "provider_unavailable"
                queue_item.locked_by = None
                queue_item.locked_at = None
                queue_item.lease_expires_at = None
                queue_item.processing_started_at = None
                session.commit()

                record_audit_log(
                    session=session,
                    actor="system:runner",
                    action="provider_unavailable_hold",
                    object_type="outbound_queue",
                    object_id=str(queue_item.id),
                    reason=AuditReason.VALIDATOR_HOLD,
                    prev_state="rendering",
                    new_state="held",
                )
                session.commit()
                return {"status": "held", "reason": "The AI service was unavailable"}

            # Retry cap reached: show "Waiting for the AI service", retries slowly
            queue_item.status = "eligible"
            queue_item.hold_reason = "Waiting for the AI service"
            queue_item.due_at = now + timedelta(minutes=5)
            queue_item.locked_by = None
            queue_item.locked_at = None
            queue_item.lease_expires_at = None
            queue_item.processing_started_at = None
            session.commit()
            return {"status": "waiting_for_ai", "reason": "Waiting for the AI service"}

        # Transient: release lease, schedule backoff, keep item eligible
        backoff_seconds = max(15.0, float(prov_err.retry_after or 15.0))
        queue_item.status = "eligible"
        queue_item.hold_reason = f"Paused: {plain_msg}"
        queue_item.due_at = now + timedelta(seconds=backoff_seconds)
        queue_item.locked_by = None
        queue_item.locked_at = None
        queue_item.lease_expires_at = None
        queue_item.processing_started_at = None
        session.commit()
        return {"status": "provider_retry", "reason": plain_msg}

    result_status = a2_state.get("status")

    if result_status == "emitted":
        # Work loop heartbeat before sending
        heartbeat(session, queue_item.id, worker_id=worker_id)

        # Check sending active AGAIN right before dispatching (§Section D)
        pre_send_active, pre_send_reason = is_sending_active(session, segment=contact.segment)
        if not pre_send_active:
            # Revert claimed task gracefully without dispatching
            queue_item.status = "eligible"
            queue_item.locked_by = None
            queue_item.locked_at = None
            queue_item.lease_expires_at = None
            queue_item.processing_started_at = None
            session.commit()
            logger.warning(f"Send cancelled right before dispatch: {pre_send_reason}")
            return {"status": "sending_stopped_pre_send", "reason": pre_send_reason}

        metadata = a2_state.get("metadata")
        subject = metadata.subject if metadata else "Narayani Distributors"
        body_text = a2_state.get("full_email_body") or a2_state.get("draft_body", "")

        queue_item.attempts = a2_state.get("attempt", 1)
        queue_item.attempt_history = a2_state.get("attempt_history", [])

        # Check catalogue attachment and wording alignment (§Phase 9)
        att_dict, fallback_url = resolve_send_attachment(queue_item, session)
        attachments = [att_dict] if att_dict else None
        if fallback_url and fallback_url not in body_text:
            body_text = f"{body_text}\n\nYou can view our catalogue online here: {fallback_url}"

        is_aligned, align_err = validate_attachment_text_alignment(body_text, has_attachment=bool(att_dict))
        if not is_aligned:
            queue_item.status = "held"
            queue_item.hold_reason = align_err
            queue_item.failed_validator = "attachment_text_alignment"
            queue_item.rendered_subject = subject
            queue_item.rendered_body = body_text
            queue_item.locked_by = None
            queue_item.locked_at = None
            queue_item.lease_expires_at = None
            queue_item.processing_started_at = None
            session.commit()
            record_audit_log(
                session=session,
                actor="pipeline",
                action="item_held_attachment_mismatch",
                object_type="outbound_queue",
                object_id=str(queue_item.id),
                reason=AuditReason.RULEBOOK_FAILED,
                prev_state="eligible",
                new_state="held",
            )
            session.commit()
            logger.warning(f"Queue item {queue_item.id} held: {align_err}")
            return {
                "queue_item_id": queue_item.id,
                "contact_id": contact.id,
                "email": contact.email,
                "status": "held",
                "reason": align_err,
            }

        send_key, send_result = execute_send(
            session=session,
            queue_item_id=queue_item.id,
            subject=subject,
            body_text=body_text,
            provider=provider,
            attachments=attachments,
        )

        return {
            "queue_item_id": queue_item.id,
            "contact_id": contact.id,
            "email": contact.email,
            "status": "sent" if send_result.success else "send_failed",
            "send_key": send_key.key,
            "provider_message_id": send_result.provider_message_id,
            "attempts": a2_state.get("attempt"),
        }

    elif result_status == "held":
        queue_item.status = "held"
        queue_item.attempts = a2_state.get("attempt", 1)
        queue_item.hold_reason = a2_state.get("hold_reason")
        queue_item.failed_validator = a2_state.get("failed_validator")
        queue_item.attempt_history = a2_state.get("attempt_history", [])
        meta = a2_state.get("metadata")
        queue_item.rendered_subject = meta.subject if meta else "Narayani Distributors"
        queue_item.rendered_body = a2_state.get("full_email_body") or a2_state.get("draft_body", "")
        queue_item.locked_by = None
        queue_item.locked_at = None
        queue_item.lease_expires_at = None
        queue_item.processing_started_at = None
        session.commit()

        record_audit_log(
            session=session,
            actor="a2_personalization",
            action="validator_hold",
            object_type="outbound_queue",
            object_id=str(queue_item.id),
            reason=AuditReason.VALIDATOR_HOLD,
            prev_state="rendering",
            new_state="held",
        )
        session.commit()
        return {
            "queue_item_id": queue_item.id,
            "contact_id": contact.id,
            "email": contact.email,
            "status": "held",
            "hold_reason": a2_state.get("hold_reason"),
            "failed_validator": a2_state.get("failed_validator"),
            "attempts": a2_state.get("attempt"),
        }

    elif result_status == "vetoed":
        veto_reason = a2_state.get("veto_reason") or "Prospect business does not fit segment criteria"
        queue_item.veto_count = (queue_item.veto_count or 0) + 1
        queue_item.attempts = a2_state.get("attempt", 1)
        queue_item.attempt_history = a2_state.get("attempt_history", [])
        meta = a2_state.get("metadata")
        if meta and meta.subject:
            queue_item.rendered_subject = meta.subject
        queue_item.rendered_body = a2_state.get("full_email_body") or a2_state.get("draft_body", "")
        queue_item.locked_by = None
        queue_item.locked_at = None
        queue_item.lease_expires_at = None
        queue_item.processing_started_at = None

        # 3 vetoes in a row go to a human review with plain reason (§6)
        if queue_item.veto_count >= 3:
            queue_item.status = "held"
            queue_item.failed_validator = "veto"
            queue_item.hold_reason = f"Prospect vetoed 3 times by AI: {veto_reason}. Needs human review."
            session.commit()

            record_audit_log(
                session=session,
                actor="a2_personalization",
                action="veto_held_for_review",
                object_type="outbound_queue",
                object_id=str(queue_item.id),
                reason=AuditReason.VETO_LIMIT,
                prev_state="rendering",
                new_state="held",
            )
            session.commit()
            return {
                "queue_item_id": queue_item.id,
                "contact_id": contact.id,
                "email": contact.email,
                "status": "held",
                "hold_reason": queue_item.hold_reason,
                "veto_count": queue_item.veto_count,
            }
        else:
            # Return item to the pool with decay (e.g. 24h delay)
            now = get_utc_now()
            queue_item.status = "eligible"
            queue_item.due_at = now + timedelta(hours=24)
            queue_item.hold_reason = f"Vetoed ({queue_item.veto_count}/3): {veto_reason}. Returning to pool with 24h delay."
            session.commit()

            record_audit_log(
                session=session,
                actor="a2_personalization",
                action="veto_decay_return",
                object_type="outbound_queue",
                object_id=str(queue_item.id),
                reason=AuditReason.VETO_LIMIT,
                prev_state="rendering",
                new_state="eligible",
            )
            session.commit()
            return {
                "queue_item_id": queue_item.id,
                "contact_id": contact.id,
                "email": contact.email,
                "status": "eligible",
                "veto_reason": veto_reason,
                "veto_count": queue_item.veto_count,
            }

    return None


def run_pipeline_batch(
    session: Session,
    worker_id: str,
    provider: EmailProvider,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Runs a batch of queue items through the pipeline up to limit."""
    results = []
    for _ in range(limit):
        item_res = run_single_pipeline_item(session, worker_id, provider)
        if not item_res:
            break
        results.append(item_res)
        if item_res.get("status") in (
            "sending_stopped",
            "auto_stop_halted",
            "ai_service_unavailable",
            "paused_daily_tokens_exhausted",
        ):
            break
    return results
