import smtplib
import uuid
import logging
from email.message import EmailMessage
from typing import Optional, Dict, Any, Set, List
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AuditReason, record_audit_log
from app.pipeline.providers.base import EmailProvider, ProviderSendResult

logger = logging.getLogger("smtp_provider")

# Hard allowlist enforced in adapter code (§18.2) — cannot be bypassed by config
SEED_ALLOWLIST: Set[str] = {
    "avnimittal900+prospect1@gmail.com",
    "avnimittal900+prospect2@gmail.com",
    "avnimittal900+prospect3@gmail.com",
    "avnimittal900+prospect4@gmail.com",
    "avnimittal900+prospect5@gmail.com",
    "avnimittal900+prospect6@gmail.com",
    "avnimittal900+prospect7@gmail.com",
    "avnimittal900+prospect8@gmail.com",
    "avnimittal900+prospect9@gmail.com",
    "avnimittal900@gmail.com",
    # Additional real test recipients (Round 2 — test-prospects-17)
    "avnimittal175@gmail.com",
    "bhoomi.thakur168@gmail.com",
    "abhinavusesthis@gmail.com",
    "ayushofficialuse@gmail.com",
    "abhinavarcade70@gmail.com",
    "mittalavni019@gmail.com",
    "mittalavni89@gmail.com",
}


class SeedAllowlistViolationError(PermissionError):
    """Raised when an email send targets a recipient outside SEED_ALLOWLIST while LIVE_EMAIL_SENDING=false (§18.2)."""
    pass


class SMTPEmailProvider(EmailProvider):
    """
    SMTP Email Provider supporting Gmail / custom SMTP.
    Enforces §18.2 hard seed allowlist directly in adapter code.
    Stamps X-Narayani-Send-Key header on all outbound emails for crash reconciliation (§14.1d).
    """

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
    ):
        self.smtp_host = smtp_host or settings.smtp_host
        self.smtp_port = smtp_port or settings.smtp_port
        self.smtp_user = smtp_user or settings.smtp_gmail
        self.smtp_password = (smtp_password or settings.smtp_password).replace(" ", "")

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
        normalized_recipient = to_email.strip().lower()

        # Hard Allowlist Enforcement (§18.2)
        if not settings.live_email_sending:
            if normalized_recipient not in SEED_ALLOWLIST:
                # Write Audit Row at the type level (§14.8f)
                if session is not None:
                    try:
                        record_audit_log(
                            session=session,
                            actor="smtp_provider",
                            action="seed_send_blocked",
                            object_type="contact",
                            object_id=str(contact_id or to_email),
                            reason=AuditReason.SEED_ALLOWLIST_VIOLATION,
                            prev_state="eligible",
                            new_state="blocked",
                        )
                        session.commit()
                    except Exception as audit_err:
                        logger.error(f"Failed to record audit log for seed allowlist violation: {audit_err}")

                error_msg = (
                    f"SEED_ALLOWLIST violation: '{to_email}' is not permitted while LIVE_EMAIL_SENDING=false (§18.2). "
                    f"Send aborted and audited."
                )
                logger.error(error_msg)
                raise SeedAllowlistViolationError(error_msg)

        # Build Email Message
        msg = EmailMessage()
        from_address = f"Narayani Distributors <{self.smtp_user}>"
        msg["From"] = from_address
        msg["To"] = to_email
        msg["Subject"] = subject

        # Stamp custom idempotency header (§14.1d)
        send_key = None
        if custom_headers:
            for header_name, header_value in custom_headers.items():
                msg[header_name] = header_value
                if header_name.lower() == "x-narayani-send-key":
                    send_key = header_value

        # Stamp Message-ID
        domain = self.smtp_user.split("@")[-1] if "@" in self.smtp_user else "narayani.example"
        msg_id_token = send_key or uuid.uuid4().hex
        msg["Message-ID"] = f"<{msg_id_token}@{domain}>"

        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        # Add attachments if any (§Phase 9)
        if attachments:
            for att in attachments:
                content = att.get("content")
                filename = att.get("filename", "catalogue.pdf")
                content_type = att.get("content_type", "application/pdf")
                if content:
                    maintype, subtype = content_type.split("/", 1) if "/" in content_type else ("application", "octet-stream")
                    msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

        # Transmit via SMTP
        try:
            logger.info(f"Connecting to SMTP {self.smtp_host}:{self.smtp_port} for {to_email}...")
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            logger.info(f"Email successfully delivered to {to_email} via SMTP. Message-ID: {msg['Message-ID']}")
            return ProviderSendResult(
                success=True,
                provider_message_id=msg["Message-ID"],
                error=None,
            )
        except Exception as e:
            logger.error(f"SMTP send failed to {to_email}: {e}")
            return ProviderSendResult(
                success=False,
                provider_message_id=msg.get("Message-ID", ""),
                error=str(e),
            )
