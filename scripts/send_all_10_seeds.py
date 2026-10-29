import sys
import logging
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, SendKey
from app.pipeline.queue import enqueue_message
from app.pipeline.providers.smtp_provider import SMTPEmailProvider
from app.pipeline.runner import run_single_pipeline_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_sender")


def send_all_10_seeds():
    print("=" * 80)
    print("STAGE 5: REAL SEED SENDING TO ALL 10 TEST ADDRESSES")
    print("=" * 80)

    # 1. Enqueue all 10 contacts
    with get_db_session() as db:
        for contact_id in range(1, 11):
            contact = db.query(Contact).filter(Contact.id == contact_id).first()
            if not contact:
                print(f"Warning: Contact ID {contact_id} not found.")
                continue

            existing_queue = (
                db.query(OutboundQueue)
                .filter(OutboundQueue.contact_id == contact_id, OutboundQueue.status.in_(["eligible", "rendering"]))
                .first()
            )
            if not existing_queue:
                q_item = enqueue_message(db, contact_id=contact.id, campaign_step="intro")
                print(f"Enqueued Contact {contact_id}: {contact.company_name} ({contact.email}) -> Queue ID: {q_item.id}")
            else:
                print(f"Contact {contact_id}: {contact.company_name} already in queue ({existing_queue.status})")

    # 2. Initialize real SMTP Provider
    smtp_provider = SMTPEmailProvider()
    print(f"\nInitialized SMTPEmailProvider with sender: {smtp_provider.smtp_user}")
    print("Sending through full pipeline (A2 -> Validators -> Pre-commit SendKey -> SMTP -> Mark Sent)...\n")

    results = []

    # 3. Process each item one by one
    for idx in range(1, 11):
        print(f"\n--- PROCESSING PROSPECT {idx} / 10 ---")
        with get_db_session() as db:
            result = run_single_pipeline_item(
                session=db,
                worker_id=f"seed_worker_{idx}",
                provider=smtp_provider,
            )
            if result:
                results.append(result)
                print(f"Prospect {idx} Status: {result.get('status').upper()}")
                print(f"Email: {result.get('email')}")
                if result.get("send_key"):
                    print(f"SendKey: {result.get('send_key')}")
                if result.get("provider_message_id"):
                    print(f"Provider Message-ID: {result.get('provider_message_id')}")
                if result.get("hold_reason"):
                    print(f"Hold Reason: {result.get('hold_reason')}")
            else:
                print(f"No item processed for step {idx}.")

    print("\n" + "=" * 80)
    print("FINAL SEED SENDING SUMMARY:")
    print("=" * 80)
    for res in results:
        status_sym = "[SENT]" if res.get("status") == "sent" else f"[{res.get('status').upper()}]"
        print(f"{status_sym} Contact {res.get('contact_id')} -> {res.get('email')} | Message-ID: {res.get('provider_message_id', 'N/A')}")
    print("=" * 80)
    return results


if __name__ == "__main__":
    send_all_10_seeds()
