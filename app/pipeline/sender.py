import uuid
import inspect
import logging
from typing import Optional, Dict, Any, Tuple, List
from sqlalchemy.orm import Session

from app.db.models import OutboundQueue, Contact, SendKey
from app.pipeline.providers.base import EmailProvider, ProviderSendResult
from app.pipeline.catalogue_service import resolve_send_attachment

logger = logging.getLogger("sender")


def execute_send(
    session: Session,
    queue_item_id: int,
    subject: str,
    body_text: str,
    provider: EmailProvider,
    body_html: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[SendKey, ProviderSendResult]:
    """
    Executes outbound send adhering strictly to §14.1d protocol:
    1. INSERT send_key row, status='sending' ← committed BEFORE the provider call
    2. call provider with X-Narayani-Send-Key header
    3. UPDATE status='sent', store provider message id & commit
    """
    # 1. Fetch queue item and contact
    queue_item = session.query(OutboundQueue).filter(OutboundQueue.id == queue_item_id).first()
    if not queue_item:
        raise ValueError(f"OutboundQueue item {queue_item_id} not found.")

    contact = session.query(Contact).filter(Contact.id == queue_item.contact_id).first()
    if not contact:
        raise ValueError(f"Contact ID {queue_item.contact_id} not found for queue item {queue_item_id}.")

    # 2. Generate unique send_key token
    send_key_token = f"sk_{queue_item.id}_{uuid.uuid4().hex[:12]}"

    # Check if send_key already exists
    existing_send_key = (
        session.query(SendKey)
        .filter(SendKey.queue_item_id == queue_item.id, SendKey.status == "sent")
        .first()
    )
    if existing_send_key:
        logger.warning(f"Queue item {queue_item_id} already has a sent send_key: {existing_send_key.key}")
        return existing_send_key, ProviderSendResult(success=True, provider_message_id=existing_send_key.provider_message_id or "")

    # STEP 1 (§14.1d): INSERT send_key status='sending' AND COMMIT BEFORE PROVIDER CALL
    send_key_record = SendKey(
        key=send_key_token,
        queue_item_id=queue_item.id,
        contact_id=contact.id,
        status="sending",
        retention_class="provider_message_ids",
    )
    session.add(send_key_record)
    session.flush()

    queue_item.status = "sending"
    queue_item.send_key_id = send_key_record.id
    session.commit()  # <-- ABSOLUTE COMMIT BEFORE CALLING PROVIDER (§14.1d)

    # Resolve attachments if not explicitly provided
    attachment_list = attachments
    if attachment_list is None:
        att_dict, fallback_url = resolve_send_attachment(queue_item, session)
        if att_dict:
            attachment_list = [att_dict]
        elif fallback_url and fallback_url not in body_text:
            body_text = f"{body_text}\n\nYou can view our catalogue online here: {fallback_url}"

    # STEP 2 (§14.1d): Call provider with X-Narayani-Send-Key header
    custom_headers = {
        "X-Narayani-Send-Key": send_key_token,
    }
    
    send_kwargs = {
        "to_email": contact.email,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "custom_headers": custom_headers,
        "session": session,
        "contact_id": contact.id,
    }
    try:
        sig = inspect.signature(provider.send_email)
        if "attachments" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            send_kwargs["attachments"] = attachment_list
    except Exception:
        pass

    send_result = provider.send_email(**send_kwargs)

    # STEP 3 (§14.1d): On success, update send_key and queue item to 'sent'
    if send_result.success:
        send_key_record.status = "sent"
        send_key_record.provider_message_id = send_result.provider_message_id
        
        queue_item.status = "sent"
        queue_item.hold_reason = None
        queue_item.failed_validator = None
        queue_item.rendered_subject = subject
        queue_item.rendered_body = body_text
        if attachment_list and len(attachment_list) > 0:
            queue_item.attachment_checksum = attachment_list[0].get("checksum")
        session.commit()
        logger.info(f"Send successfully recorded and committed for queue item {queue_item_id} (key={send_key_token}).")
    else:
        logger.error(f"Send failed for queue item {queue_item_id}: {send_result.error}")
        # Note: send_key remains status='sending' for crash reconciliation / reaper (§14.1d)
        session.commit()

    return send_key_record, send_result
