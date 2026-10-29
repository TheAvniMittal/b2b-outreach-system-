from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session


@dataclass
class ProviderSendResult:
    success: bool
    provider_message_id: str
    error: Optional[str] = None


class EmailProvider(ABC):
    @abstractmethod
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
        """
        Sends an email through the provider.
        custom_headers must include 'X-Narayani-Send-Key' for idempotency & crash reconciliation (§14.1d).
        attachments is a list of dicts: {'filename': str, 'content': bytes, 'checksum': str, 'size_bytes': int}.
        """
        pass
