from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue
from app.pipeline.queue import enqueue_message
from app.pipeline.providers.smtp_provider import SMTPEmailProvider
from app.pipeline.runner import run_single_pipeline_item

def send_1_and_3():
    with get_db_session() as db:
        for cid in [1, 3]:
            # Reset or create eligible queue item
            q = db.query(OutboundQueue).filter(OutboundQueue.contact_id == cid).first()
            if not q:
                q = enqueue_message(db, contact_id=cid, campaign_step="intro")
            else:
                q.status = "eligible"
                q.attempts = 0
            db.commit()

        provider = SMTPEmailProvider()
        print("Sending Contact 1 and Contact 3 via SMTP...")
        for cid in [1, 3]:
            print(f"\n--- Running Pipeline for Contact {cid} ---")
            res = run_single_pipeline_item(
                session=db,
                worker_id=f"worker_send_{cid}",
                provider=provider,
            )
            print("Result:", res)

if __name__ == "__main__":
    send_1_and_3()
