import logging
import uuid
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from app.pipeline.providers.base import EmailProvider, ProviderSendResult

logger = logging.getLogger("mock_provider")


class MockEmailProvider(EmailProvider):
    """
    Mock Email Provider (Stage 5 Step 1).
    Logs what WOULD be sent instead of sending anything.
    Tracks sent messages in memory for test assertions.
    """

    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        session: Optional[Session] = None,
        contact_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> ProviderSendResult:
        mock_msg_id = f"mock_msg_{uuid.uuid4().hex[:12]}"
        
        message_record = {
            "to_email": to_email,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "custom_headers": custom_headers or {},
            "provider_message_id": mock_msg_id,
            "contact_id": contact_id,
            "attachments": attachments or [],
        }
        self.sent_messages.append(message_record)
        
        att_str = ""
        if attachments:
            att_details = [
                f"{a.get('filename')} ({a.get('size_bytes')}B, sha256={a.get('checksum', '')[:12]}...)"
                for a in attachments
            ]
            att_str = f" | Attachments: {', '.join(att_details)}"

        logger.info(
            f"[MOCK SEND] To: {to_email} | Subject: {subject} | "
            f"Send-Key: {custom_headers.get('X-Narayani-Send-Key') if custom_headers else 'None'} | "
            f"Message-ID: {mock_msg_id}{att_str}"
        )

        return ProviderSendResult(
            success=True,
            provider_message_id=mock_msg_id,
            error=None,
        )

    def clear(self):
        self.sent_messages.clear()
