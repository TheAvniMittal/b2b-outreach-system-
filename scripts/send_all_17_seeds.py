"""
Seed sending runner for all 17 test prospect addresses (Round 1 + Round 2).
Enforces the 17-address hard allowlist directly in SMTPEmailProvider.

Usage:
  # Send only to the 7 new real test inboxes (Rows 11-17):
  python scripts/send_all_17_seeds.py --new-only

  # Send to all 17 test inboxes (Rows 1-17):
  python scripts/send_all_17_seeds.py --all

  # Generate and validate drafts without sending (dry-run):
  python scripts/send_all_17_seeds.py --dry-run
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, SendKey
from app.pipeline.import_csv import import_prospects_csv
from app.pipeline.queue import enqueue_message
from app.pipeline.providers.smtp_provider import SMTPEmailProvider, SEED_ALLOWLIST
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.pipeline.runner import run_single_pipeline_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_sender_17")

NEW_7_EMAILS = [
    "avnimittal175@gmail.com",
    "bhoomi.thakur168@gmail.com",
    "abhinavusesthis@gmail.com",
    "ayushofficialuse@gmail.com",
    "abhinavarcade70@gmail.com",
    "mittalavni019@gmail.com",
    "mittalavni89@gmail.com",
]


def run_seed_sending(mode: str = "new-only", dry_run: bool = False):
    print("=" * 80)
    print("🚀 NARAYANI OUTREACH — SEED SENDING RUNNER (17-RECIPIENT SUITE)")
    print(f"Mode: {mode.upper()} | Dry-Run: {dry_run}")
    print("=" * 80)

    # 1. Ensure CSV is imported
    csv_path = Path(__file__).resolve().parent.parent / "data" / "test_prospects_17.csv"
    with get_db_session() as db:
        report = import_prospects_csv(db, csv_path)
        db.commit()
        print(f"📁 CSV Ingestion: {report.imported_count} contacts verified & updated.")

    # 2. Determine target contacts
    with get_db_session() as db:
        if mode == "new-only":
            raw_contacts = db.query(Contact).filter(Contact.email.in_(NEW_7_EMAILS)).order_by(Contact.id.desc()).all()
        else:
            raw_contacts = db.query(Contact).filter(Contact.email.in_(list(SEED_ALLOWLIST))).order_by(Contact.id.desc()).all()

        # Deduplicate contacts by email (e.g. if duplicate test fixtures exist, pick latest by id)
        seen_emails = set()
        target_contacts = []
        for c in raw_contacts:
            em = c.email.strip().lower()
            if em not in seen_emails:
                seen_emails.add(em)
                target_contacts.append(c)

        print(f"🎯 Target recipients for this run ({len(target_contacts)}):")
        for c in target_contacts:
            print(f"   • [{c.id}] {c.contact_name} ({c.company_name}) -> {c.email} [{c.segment}]")

        # 3. Enqueue target contacts
        enqueued_count = 0
        for c in target_contacts:
            # Check existing queue item (including held and sent)
            existing = (
                db.query(OutboundQueue)
                .filter(
                    OutboundQueue.contact_id == c.id,
                    OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
                )
                .first()
            )
            if not existing:
                q_item = enqueue_message(db, contact_id=c.id, campaign_step="intro")
                enqueued_count += 1
                print(f"   ➕ Enqueued contact {c.id} ({c.email}) -> Queue ID: {q_item.id}")
            else:
                print(f"   ℹ️ Contact {c.id} ({c.email}) already in queue ({existing.status})")

        db.commit()

    # 4. Initialize Provider
    if dry_run:
        provider = MockEmailProvider()
        print("\n🧪 Using MockEmailProvider (Dry-run mode — no live emails will be dispatched)")
    else:
        provider = SMTPEmailProvider()
        print(f"\n📨 Using SMTPEmailProvider via {provider.smtp_host}:{provider.smtp_port} as '{provider.smtp_user}'")
        print("🔒 Hard Allowlist: Active (17 approved seed addresses)")

    print("\nStarting pipeline execution cycle (A2 Personalization -> 4 Validators -> Pre-commit -> Dispatch)...\n")

    # 5. Process queue items
    results = []
    processed_count = 0
    total_to_process = len(target_contacts)

    while processed_count < total_to_process:
        with get_db_session() as db:
            result = run_single_pipeline_item(
                session=db,
                worker_id=f"worker_17_seeds_{processed_count + 1}",
                provider=provider,
            )
            if not result:
                # No more eligible items
                break

            processed_count += 1
            results.append(result)

            status_str = result.get("status", "").upper()
            email_str = result.get("email")
            subj_str = result.get("rendered_subject")
            send_key_str = result.get("send_key")
            msg_id_str = result.get("provider_message_id")

            print(f"[{processed_count}/{total_to_process}] Recipient: {email_str} | Status: {status_str}")
            if subj_str:
                print(f"       Subject:     {subj_str}")
            if send_key_str:
                print(f"       SendKey:     {send_key_str}")
            if msg_id_str:
                print(f"       Message-ID:  {msg_id_str}")
            if result.get("hold_reason"):
                print(f"       Hold Reason: {result.get('hold_reason')}")
            if result.get("veto_reason"):
                print(f"       Veto Reason: {result.get('veto_reason')}")
            print("-" * 70)

    # 6. Summary
    print("\n" + "=" * 80)
    print(f"🏁 RUN COMPLETED: {len(results)} prospect email(s) processed.")
    sent_success = sum(1 for r in results if r.get("status") == "sent")
    print(f"   • Successfully Sent: {sent_success}")
    print(f"   • Held for Review:   {sum(1 for r in results if r.get('status') == 'held')}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Send test emails to the 17 allowlisted addresses")
    parser.add_argument("--new-only", action="store_true", default=True, help="Send only to the 7 new real inboxes (Rows 11-17)")
    parser.add_argument("--all", action="store_true", help="Send to all 17 allowlisted test inboxes")
    parser.add_argument("--dry-run", action="store_true", help="Run A2 drafts & validators without dispatching SMTP")

    args = parser.parse_args()
    mode = "all" if args.all else "new-only"
    run_seed_sending(mode=mode, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
