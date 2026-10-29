"""
CLI tool to inspect all contacts and queue items requiring human review.

Usage:
  python scripts/inspect_holds.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, AuditLog


def inspect_holds():
    print("\n=======================================================")
    print("🔍 NARAYANI OUTREACH — HUMAN REVIEW INSPECTOR")
    print("=======================================================\n")

    with get_db_session() as session:
        # 1. Contacts in 'held' status (salesperson takeover from positive reply)
        held_contacts = (
            session.query(Contact)
            .filter(Contact.status == "held")
            .order_by(Contact.updated_at.desc())
            .all()
        )

        print(f"📬 1. PROSPECT REPLIES NEEDING SALES ATTENTION ({len(held_contacts)}):")
        print("-" * 55)
        if not held_contacts:
            print("   ✅ No contacts currently on human hold.")
        else:
            for c in held_contacts:
                print(f"   👤 Contact ID:   {c.id}")
                print(f"      Name:         {c.contact_name} ({c.designation or 'N/A'})")
                print(f"      Company:      {c.company_name} [{c.segment}]")
                print(f"      Email:        {c.email}")
                print(f"      Status:       {c.status.upper()} (Automated outreach stopped)")
                if c.notes:
                    print(f"      Notes:        {c.notes}")
                if c.product_interest:
                    print(f"      Interest:     {c.product_interest}")
                print(f"      Updated At:   {c.updated_at}")
                print()

        # 2. Queue items in 'held' status (A2 drafts that hit veto limit or failed validators)
        held_queue = (
            session.query(OutboundQueue)
            .filter(OutboundQueue.status == "held")
            .order_by(OutboundQueue.updated_at.desc())
            .all()
        )

        print(f"\n📝 2. EMAIL DRAFTS HELD FOR COPYWRITING REVIEW ({len(held_queue)}):")
        print("-" * 55)
        if not held_queue:
            print("   ✅ No outbound drafts stuck on veto limit.")
        else:
            for q in held_queue:
                contact = session.query(Contact).filter_by(id=q.contact_id).first()
                c_name = contact.contact_name if contact else f"Contact {q.contact_id}"
                c_comp = contact.company_name if contact else "Unknown"

                print(f"   📄 Queue Item ID: {q.id}")
                print(f"      Recipient:     {c_name} ({c_comp})")
                print(f"      Attempts:      {q.attempts}")
                print(f"      Subject:       {q.rendered_subject or 'Draft not generated'}")
                if q.rendered_body:
                    print(f"      Body preview:  {q.rendered_body[:120]}...")
                print(f"      Updated At:    {q.updated_at}")
                print()

        # 3. Recent Audit Holds (Today)
        recent_holds = (
            session.query(AuditLog)
            .filter(AuditLog.reason.in_(["validator_hold", "veto_limit"]))
            .order_by(AuditLog.timestamp.desc())
            .limit(5)
            .all()
        )
        print(f"\n🛡️ 3. RECENT AUDIT LOG VETOES / HOLDS ({len(recent_holds)}):")
        print("-" * 55)
        if not recent_holds:
            print("   ✅ No validator vetoes logged recently.")
        else:
            for a in recent_holds:
                print(f"   • [{a.timestamp}] Actor: {a.actor} | Action: {a.action} | Object: {a.object_type}:{a.object_id} | Reason: {a.reason.value}")

    print("\n=======================================================\n")


if __name__ == "__main__":
    inspect_holds()
