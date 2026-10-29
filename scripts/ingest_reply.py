"""
Inbound Reply Ingestion & A3 Verification Script (VISION.md §7.5, §18.2).

Allows ingesting inbound email replies:
  1. Interactive / CLI argument mode:
     python scripts/ingest_reply.py --from avnimittal900+prospect1@gmail.com --subject "Re: Quick intro" --body "Yes, please send over the catalogue and wholesale price list."
  2. Live IMAP Fetch mode:
     python scripts/ingest_reply.py --live
     Connects to Gmail IMAP using SMTP_GMAIL and SMTP_PASSWORD from .env, finds new/unseen replies, and runs A3.
"""

import os
import sys

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import argparse
import imaplib
import email
from email.header import decode_header
from typing import Optional, Dict

import dotenv
dotenv.load_dotenv()
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, SendKey, Suppression
from app.workers.a3_reply_classifier.service import classify_inbound_reply
from app.schemas.a3_contract import A3ReplyContract


def decode_str(header_val: Optional[str]) -> str:
    """Decodes MIME encoded header strings."""
    if not header_val:
        return ""
    decoded_fragments = decode_header(header_val)
    parts = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            parts.append(fragment.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(str(fragment))
    return "".join(parts)


def process_reply(
    from_email: str,
    subject: str,
    body: str,
    headers: Optional[Dict[str, str]] = None,
):
    """Processes a single inbound reply through A3 classifier and DB updates."""
    clean_email = from_email.strip().lower()
    # Handle formats like "First Last <user@domain.com>"
    if "<" in clean_email and ">" in clean_email:
        clean_email = clean_email.split("<")[1].split(">")[0].strip()

    print(f"\n==================================================")
    print(f"📥 Processing Inbound Reply from: {clean_email}")
    print(f"Subject: {subject}")
    print(f"Body snippet: {body[:120]}...")
    print(f"==================================================")

    with get_db_session() as session:
        contact = session.query(Contact).filter(Contact.email == clean_email).first()
        if not contact:
            print(f"⚠️ Warning: Contact with email '{clean_email}' not found in DB.")
            # Search by prefix/alias if prospect alias
            contacts = session.query(Contact).filter(Contact.email.like("avnimittal900%@gmail.com")).all()
            if contacts:
                contact = contacts[0]
                print(f"ℹ️ Defaulting to test seed contact ID={contact.id} ({contact.email}) for verification demonstration.")
            else:
                print("❌ No contacts found in database. Please run prospect import first.")
                return

        print(f"👤 Matched Contact: ID={contact.id} | Name={contact.contact_name} | Company={contact.company_name} | Segment={contact.segment}")
        print(f"   Current Status: {contact.status}")

        contract, actions = classify_inbound_reply(
            session=session,
            contact=contact,
            body=body,
            subject=subject,
            headers=headers or {},
            segment=contact.segment,
        )

        # Store reply summary in notes for operator visibility on the UI
        existing_notes = (contact.notes or "").strip()
        reply_tag = f"[Reply - {contract.intent.value.upper()} (conf {contract.confidence:.2f})]: {contract.summary or body[:140]}"
        contact.notes = f"{existing_notes}\n{reply_tag}".strip() if existing_notes else reply_tag

        session.commit()

        # Re-fetch contact to show post-classification state
        session.refresh(contact)

        print("\n🎯 A3 Classification Result:")
        print(f"   - Kind:               {contract.kind.value}")
        print(f"   - Intent:             {contract.intent.value}")
        print(f"   - Confidence:         {contract.confidence:.2f}")
        print(f"   - Suppression Signal: {contract.suppression_signal.value}")
        print(f"   - Summary:            {contract.summary}")
        print(f"   - Evidence Quote:     {contract.evidence_quote}")
        print(f"   - Classification Src: {actions.get('source')}")

        print("\n⚙️ Mechanical Overrides & Actions Executed:")
        print(f"   - Contact New Status: {contact.status}")
        print(f"   - Suppressed:         {actions.get('suppressed')}")
        print(f"   - Automation Paused:  {actions.get('automation_paused')}")
        print(f"   - Routed to Human:    {actions.get('routed_to_human')}")

        if actions.get('suppressed'):
            print(f"   🛡️ Permanent suppression entry written to 'suppression' table.")
        if actions.get('automation_paused'):
            print(f"   ⏸️ Outbound automation stopped for this contact (human takeover).")

    return contract, actions


def fetch_live_replies(limit: int = 50):
    """Connects to Gmail IMAP and processes all inbound replies."""
    username = (os.environ.get("SMTP_GMAIL") or os.environ.get("SMTP_gmail") or settings.smtp_gmail or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_password") or settings.smtp_password or "").strip().replace(" ", "")

    if not username or not password:
        print("❌ Error: SMTP_GMAIL or SMTP_PASSWORD not set in environment.")
        return

    print(f"🔌 Connecting to Gmail IMAP (imap.gmail.com:993) as {username}...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(username, password)
        mail.select("INBOX")

        # Load known contact emails from DB
        with get_db_session() as session:
            known_contacts = {c.email.strip().lower() for c in session.query(Contact.email).all()}

        # Search for unseen messages or recent messages
        status, messages = mail.search(None, 'UNSEEN')
        msg_ids = messages[0].split()

        if not msg_ids:
            # Check recent messages if no unseen
            print(f"ℹ️ No unread messages found. Checking recent {limit} messages...")
            status, messages = mail.search(None, 'ALL')
            all_ids = messages[0].split()
            msg_ids = all_ids[-limit:] if len(all_ids) >= limit else all_ids

        if not msg_ids:
            print("📭 Inbox is empty.")
            mail.logout()
            return

        print(f"🔍 Found {len(msg_ids)} candidate message(s). Examining all replies...")

        processed_count = 0
        for m_id in reversed(msg_ids):
            res, data = mail.fetch(m_id, "(RFC822)")
            if res != "OK":
                continue

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            from_hdr = decode_str(msg.get("From", ""))
            subject_hdr = decode_str(msg.get("Subject", ""))

            # Clean from address
            clean_from = from_hdr.strip().lower()
            if "<" in clean_from and ">" in clean_from:
                clean_from = clean_from.split("<")[1].split(">")[0].strip()

            # Extract body
            body_text = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body_text = payload.decode("utf-8", errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body_text = payload.decode("utf-8", errors="replace")

            # Check if this email is from a known contact OR is a reply (Re:)
            is_match = (
                clean_from in known_contacts
                or "re:" in subject_hdr.lower()
                or "narayani" in subject_hdr.lower()
                or bool(msg.get("In-Reply-To"))
            )

            # Avoid classifying self-sent emails
            if is_match and clean_from != username.lower():
                headers_dict = {
                    "Message-ID": msg.get("Message-ID", ""),
                    "In-Reply-To": msg.get("In-Reply-To", ""),
                    "References": msg.get("References", ""),
                }
                process_reply(
                    from_email=clean_from,
                    subject=subject_hdr,
                    body=body_text,
                    headers=headers_dict,
                )
                processed_count += 1

        print(f"\n🏁 Finished reply scan: Classified {processed_count} message(s).")
        mail.logout()

    except Exception as e:
        print(f"❌ IMAP check encountered an error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Ingest and classify inbound reply via A3")
    parser.add_argument("--live", action="store_true", help="Fetch live replies from Gmail inbox via IMAP")
    parser.add_argument("--limit", type=int, default=50, help="Max recent messages to check (default: 50)")
    parser.add_argument("--from-email", dest="from_email", type=str, help="Sender email address")
    parser.add_argument("--subject", type=str, default="Re: Quick intro - Narayani Distributors", help="Email subject")
    parser.add_argument("--body", type=str, help="Email body content")

    args = parser.parse_args()

    if args.live:
        fetch_live_replies(limit=args.limit)
    elif args.from_email and args.body:
        process_reply(
            from_email=args.from_email,
            subject=args.subject,
            body=args.body,
        )
    else:
        # Default verification run: simulate a positive reply from Contact 1 to verify full pipeline
        print("💡 No arguments supplied. Running verification test with a simulated prospect reply:")
        test_email = "avnimittal900+prospect1@gmail.com"
        test_body = (
            "Hi Avni,\n\n"
            "Thanks for reaching out. Yes, we are currently reviewing our confectionery inventory. "
            "Could you please share your product catalogue, MOQ, and export pricing for the UK?\n\n"
            "Best regards,\n"
            "Oliver Smith\n"
            "Procurement Manager"
        )
        process_reply(
            from_email=test_email,
            subject="Re: Quick intro - Narayani Distributors",
            body=test_body,
        )


if __name__ == "__main__":
    main()
