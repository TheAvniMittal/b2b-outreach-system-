import re
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Contact
from app.workers.a3_reply_classifier.service import classify_inbound_reply

logger = logging.getLogger("imap_listener")

# Thread-safe status tracking for management API & UI inspection
LAST_POLL_STATUS: Dict[str, Any] = {
    "last_poll_at": None,
    "status": "idle",
    "last_result": None,
    "last_error": None,
}


def decode_header_str(val: Optional[str]) -> str:
    """Decodes MIME encoded header strings into standard unicode text."""
    if not val:
        return ""
    try:
        decoded_parts = decode_header(val)
        result = []
        for text_part, enc in decoded_parts:
            if isinstance(text_part, bytes):
                result.append(text_part.decode(enc or "utf-8", errors="replace"))
            else:
                result.append(str(text_part))
        return "".join(result)
    except Exception as e:
        logger.debug(f"Failed to decode header '{val}': {e}")
        return str(val)


def extract_clean_email(raw_sender: Optional[str]) -> Optional[str]:
    """Extracts raw email address from From: header (e.g. 'Jane Doe <jane@example.com>' -> 'jane@example.com')."""
    if not raw_sender:
        return None
    _, email_addr = parseaddr(raw_sender)
    if email_addr and "@" in email_addr:
        return email_addr.lower().strip()
    match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", raw_sender)
    if match:
        return match.group(0).lower().strip()
    return None


def strip_html_tags(html_str: str) -> str:
    """Strips HTML tags to retrieve readable text from HTML-only emails."""
    text = re.sub(r"(?i)<style.*?</style>", "", html_str, flags=re.DOTALL)
    text = re.sub(r"(?i)<script.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_email_body(msg: email.message.Message) -> str:
    """Extracts plain text body from an email Message, falling back to converted HTML."""
    plain_text_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disp = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disp.lower():
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                decoded_text = payload.decode(charset, errors="replace")
            except Exception:
                decoded_text = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                plain_text_parts.append(decoded_text)
            elif content_type == "text/html":
                html_parts.append(decoded_text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded_text = payload.decode(charset, errors="replace")
            except Exception:
                decoded_text = payload.decode("utf-8", errors="replace")

            if msg.get_content_type() == "text/plain":
                plain_text_parts.append(decoded_text)
            elif msg.get_content_type() == "text/html":
                html_parts.append(decoded_text)

    if plain_text_parts:
        return "\n\n".join(plain_text_parts).strip()
    if html_parts:
        return strip_html_tags("\n\n".join(html_parts))
    return ""


def process_inbound_message(
    session: Session,
    raw_message_bytes: bytes,
) -> Dict[str, Any]:
    """
    Parses a raw RFC822 email payload, checks if sender is a recognized contact,
    and runs the A3 Reply Classifier pipeline if matched.
    """
    msg = email.message_from_bytes(raw_message_bytes)

    raw_from = decode_header_str(msg.get("From", ""))
    clean_sender = extract_clean_email(raw_from)
    subject = decode_header_str(msg.get("Subject", ""))
    in_reply_to = msg.get("In-Reply-To", "")
    references = msg.get("References", "")
    message_id = msg.get("Message-ID", "")

    headers_dict = {
        "From": raw_from,
        "Subject": subject,
        "In-Reply-To": in_reply_to,
        "References": references,
        "Message-ID": message_id,
    }

    body = extract_email_body(msg)

    if not clean_sender:
        return {
            "matched": False,
            "sender": raw_from,
            "reason": "Could not parse sender email address",
        }

    # Match prospect in PostgreSQL
    contact = (
        session.query(Contact)
        .filter(func.lower(Contact.email) == clean_sender)
        .first()
    )

    if not contact:
        logger.info(f"Inbound email from {clean_sender} not matched to any contact. Skipping A3.")
        return {
            "matched": False,
            "sender": clean_sender,
            "subject": subject,
            "reason": "Sender not found in contacts directory",
        }

    # Check if this exact reply was already ingested to avoid duplicates
    clean_snippet = body.strip().split("\nOn ")[0].strip()[:60] if body else ""
    if clean_snippet and contact.notes and clean_snippet in contact.notes:
        logger.info(f"Inbound reply from {clean_sender} already recorded in contact #{contact.id} notes. Skipping.")
        return {
            "matched": True,
            "contact_id": contact.id,
            "contact_name": contact.contact_name,
            "email": contact.email,
            "status": "already_processed",
            "message": "Email already recorded in contact notes",
        }

    # Execute A3 Classifier Pipeline
    logger.info(f"Ingesting inbound reply from contact #{contact.id} ({contact.email})")
    final_contract, actions_taken = classify_inbound_reply(
        session=session,
        contact=contact,
        body=body,
        subject=subject,
        headers=headers_dict,
    )

    # Ensure contact notes include the prospect's actual email text for operator visibility
    if body and body.strip():
        display_snippet = body.strip().split("\nOn ")[0].strip()[:300]
        prefix = f"[Inbound Reply - {getattr(final_contract.intent, 'value', str(final_contract.intent))}]:"
        if display_snippet not in (contact.notes or ""):
            contact.notes = f"{contact.notes or ''}\n{prefix} \"{display_snippet}\"".strip()

    session.commit()

    return {
        "matched": True,
        "contact_id": contact.id,
        "contact_name": contact.contact_name,
        "email": contact.email,
        "new_status": contact.status,
        "intent": getattr(final_contract.intent, "value", str(final_contract.intent)),
        "kind": getattr(final_contract.kind, "value", str(final_contract.kind)),
        "confidence": final_contract.confidence,
        "actions": actions_taken,
    }


def poll_imap_inbox(
    session: Session,
    host: str = "imap.gmail.com",
    port: int = 993,
    limit: int = 50,
    mark_seen: bool = True,
) -> Dict[str, Any]:
    """
    Connects to Gmail via IMAP over SSL, retrieves unread messages (UNSEEN),
    ingests matching prospect replies, and marks them as read.
    """
    global LAST_POLL_STATUS
    start_time = datetime.now(timezone.utc)
    LAST_POLL_STATUS["status"] = "running"

    email_user = (settings.smtp_gmail or "").strip()
    email_pass = (settings.smtp_password or "").strip()

    if not email_user or not email_pass:
        msg = "IMAP polling skipped: SMTP_gmail or SMTP_PASSWORD is not configured in .env"
        logger.warning(msg)
        LAST_POLL_STATUS["status"] = "error"
        LAST_POLL_STATUS["last_error"] = msg
        return {"success": False, "error": msg, "checked": 0, "matched": 0}

    # Clean password of spaces if user pasted with spaces
    clean_pass = email_pass.replace(" ", "")

    imap = None
    results: List[Dict[str, Any]] = []
    messages_checked = 0
    matched_count = 0

    try:
        imap = imaplib.IMAP4_SSL(host, port, timeout=20)
        imap.login(email_user, clean_pass)
        status, _ = imap.select("INBOX")
        if status != "OK":
            raise RuntimeError(f"Failed to select INBOX: {status}")

        status, search_data = imap.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"Failed to search UNSEEN messages: {status}")

        msg_ids = search_data[0].split()
        total_unseen = len(msg_ids)

        # Fallback: if no UNSEEN messages found, check the most recent 15 messages in INBOX
        # in case an operator opened the email in Gmail web/mobile before the poller ran.
        if not msg_ids:
            status_all, all_data = imap.search(None, "ALL")
            if status_all == "OK" and all_data and all_data[0]:
                all_ids = all_data[0].split()
                msg_ids = all_ids[-15:]
                logger.info(f"IMAP poll checking latest {len(msg_ids)} recent message(s) in {email_user}")
        else:
            logger.info(f"IMAP poll found {total_unseen} unseen message(s) in {email_user}")

        # Process up to limit
        for mid in msg_ids[:limit]:
            messages_checked += 1
            res, fetch_data = imap.fetch(mid, "(RFC822)")
            if res != "OK" or not fetch_data or not fetch_data[0]:
                continue

            raw_bytes = fetch_data[0][1]
            if not isinstance(raw_bytes, bytes):
                continue

            try:
                proc_res = process_inbound_message(session, raw_bytes)
                results.append(proc_res)
                if proc_res.get("matched"):
                    matched_count += 1
            except Exception as item_err:
                logger.error(f"Error processing email message ID {mid}: {item_err}", exc_info=True)
                results.append({"error": str(item_err)})

            # Mark as read
            if mark_seen:
                try:
                    imap.store(mid, "+FLAGS", "\\Seen")
                except Exception as flag_err:
                    logger.debug(f"Could not set \\Seen flag on message {mid}: {flag_err}")

        LAST_POLL_STATUS["status"] = "idle"
        LAST_POLL_STATUS["last_poll_at"] = start_time.isoformat()
        LAST_POLL_STATUS["last_error"] = None
        LAST_POLL_STATUS["last_result"] = {
            "unseen_count": total_unseen,
            "checked": messages_checked,
            "matched": matched_count,
        }

        return {
            "success": True,
            "total_unseen": total_unseen,
            "checked": messages_checked,
            "matched": matched_count,
            "results": results,
            "timestamp": start_time.isoformat(),
        }

    except Exception as exc:
        err_msg = f"IMAP polling error on {email_user}@{host}: {str(exc)}"
        logger.error(err_msg)
        LAST_POLL_STATUS["status"] = "error"
        LAST_POLL_STATUS["last_error"] = err_msg
        return {
            "success": False,
            "error": err_msg,
            "checked": messages_checked,
            "matched": matched_count,
        }
    finally:
        if imap:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass


class IMAPPollerDaemon:
    """
    Background worker daemon that polls Gmail IMAP periodically (every 60s).
    """

    def __init__(self, poll_interval_seconds: float = 60.0):
        self.poll_interval = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if not (settings.smtp_gmail and settings.smtp_password):
            logger.info("IMAP poller daemon skipped: SMTP credentials not set in .env")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="IMAPInboundPoller"
        )
        self._thread.start()
        logger.info("IMAP inbound poller daemon started.")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("IMAP inbound poller daemon stopped.")

    def _run_loop(self):
        # Initial sleep of 10s to let server boot cleanly
        self._stop_event.wait(10.0)
        while not self._stop_event.is_set():
            try:
                from app.db.session import get_db_session
                with get_db_session() as session:
                    poll_imap_inbox(session=session)
            except Exception as e:
                logger.error(f"Error in background IMAP poll loop: {e}", exc_info=True)
            self._stop_event.wait(self.poll_interval)


imap_poller_daemon = IMAPPollerDaemon(poll_interval_seconds=60.0)

