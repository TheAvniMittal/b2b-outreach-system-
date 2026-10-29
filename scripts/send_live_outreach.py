"""
Live outreach runner for Narayani B2B Pipeline.
Enqueues pending contacts and executes the pipeline (A2 Personalization -> Validators -> Pre-commit -> Dispatch).

Usage:
    py scripts/send_live_outreach.py
    py scripts/send_live_outreach.py --limit 20
    py scripts/send_live_outreach.py --dry-run
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.config import settings
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue
from app.pipeline.queue import enqueue_message
from app.pipeline.runner import run_pipeline_batch
from app.pipeline.providers.smtp_provider import SMTPEmailProvider
from app.pipeline.providers.mock_provider import MockEmailProvider


def main():
    parser = argparse.ArgumentParser(description="Narayani Live Outreach Runner")
    parser.add_argument("--limit", type=int, default=10, help="Max items to process in this run (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate generation and validation without sending live emails")
    args = parser.parse_args()

    print("=" * 80)
    print("🚀 NARAYANI OUTREACH — LIVE OUTREACH RUNNER")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE SEND'} | Limit: {args.limit}")
    print(f"Safety Flags: LIVE_EMAIL_SENDING={settings.live_email_sending} | TEST_MODE={settings.test_mode}")
    print("=" * 80)

    # 1. Enqueue pending prospects
    with get_db_session() as db:
        pending_contacts = db.query(Contact).filter(Contact.status == "pending").all()
        print(f"\n📋 Found {len(pending_contacts)} pending prospects in database.")

        enqueued_count = 0
        for c in pending_contacts:
            existing = (
                db.query(OutboundQueue)
                .filter(
                    OutboundQueue.contact_id == c.id,
                    OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
                )
                .first()
            )
            if not existing:
                q = enqueue_message(db, contact_id=c.id, campaign_step="intro")
                enqueued_count += 1
                print(f"   ➕ Enqueued: [{c.id}] {c.contact_name} ({c.company_name}) -> Queue ID: {q.id}")
            else:
                print(f"   ℹ️ [{c.id}] {c.contact_name} already in queue ({existing.status})")

        db.commit()
        print(f"✅ Enqueue completed. {enqueued_count} new items added to outbound queue.")

    # 2. Setup Provider
    if args.dry_run:
        provider = MockEmailProvider()
        print("\n🧪 Using MockEmailProvider (no live emails will be dispatched).")
    else:
        provider = SMTPEmailProvider()
        print(f"\n📨 Using SMTPEmailProvider via {provider.smtp_host}:{provider.smtp_port} as '{provider.smtp_user}'")

    # 3. Execute Pipeline Batch
    print(f"\nStarting pipeline processing (up to {args.limit} items)...\n")
    with get_db_session() as db:
        results = run_pipeline_batch(
            session=db,
            worker_id="live_runner_01",
            provider=provider,
            limit=args.limit
        )

    print("\n" + "=" * 80)
    print(f"🏁 RUN COMPLETE: Processed {len(results)} items")
    print("=" * 80)
    for r in results:
        status = r.get("status", "").upper()
        email = r.get("email")
        q_id = r.get("queue_item_id")
        attempts = r.get("attempts")
        reason = r.get("hold_reason") or r.get("veto_reason") or ""
        extra = f" | Reason: {reason}" if reason else ""
        print(f"[{status}] {email} (Queue #{q_id}, Attempt {attempts}){extra}")


if __name__ == "__main__":
    main()
