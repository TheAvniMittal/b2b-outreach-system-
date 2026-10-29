from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from freezegun import freeze_time

from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, AuditLog, AuditReason
from app.pipeline.import_csv import import_prospects_csv
from app.pipeline.queue import (
    enqueue_message,
    claim_next_outbound_task,
    run_reaper,
)

def test_stage_1_checkpoint_e2e():
    """
    CHECKPOINT:
    1. Import the 10-row CSV from test-prospects-10.md.
    2. Confirm all 10 land in contacts with verification_status='valid'.
    3. Kill a worker mid-claim and confirm the reaper recovers cleanly with no duplication.
    """
    csv_path = Path(__file__).resolve().parent.parent / "data" / "test_prospects_10.csv"
    assert csv_path.exists(), f"Test CSV not found at {csv_path}"

    with get_db_session() as session:
        # Step 1: Import 10-row CSV
        report = import_prospects_csv(session, csv_path)
        assert report.imported_count == 10, f"Expected 10 imported, got {report.imported_count}"
        assert report.rejected_count == 0, f"Expected 0 rejected, got {report.rejected_count}"

        # Step 2: Confirm all 10 land in contacts with verification_status='valid'
        imported_ids = [c.id for c in report.imported_contacts]
        assert len(imported_ids) == 10
        contacts = (
            session.query(Contact)
            .filter(Contact.id.in_(imported_ids))
            .all()
        )
        assert len(contacts) == 10
        for c in contacts:
            assert c.verification_status == "valid", f"Contact {c.email} has status {c.verification_status}"
            assert c.segment is not None
            assert c.segment != "unknown"

        # Step 3: Enqueue a task for contact 1
        c1_id = contacts[0].id
        # Clean queue for isolation
        session.query(OutboundQueue).delete()
        session.commit()

    # Step 4: Worker 1 claims task at t0
    t0 = datetime(2026, 9, 21, 17, 0, 0, tzinfo=timezone.utc)
    with freeze_time(t0) as frozen_time:
        with get_db_session() as session:
            queue_item = enqueue_message(session, contact_id=c1_id, campaign_step="intro")
            item_id = queue_item.id

            claimed = claim_next_outbound_task(session, worker_id="worker_pid_4091", lease_seconds=90)
            assert claimed is not None
            assert claimed.id == item_id
            assert claimed.status == "rendering"
            assert claimed.locked_by == "worker_pid_4091"

        # Simulating worker crash / kill mid-claim:
        # Worker 1 terminates abruptly. No heartbeat is sent.
        # Time jumps past lease expiry (+105s, lease was 90s)
        frozen_time.tick(delta=timedelta(seconds=105))

        # Reaper runs
        with get_db_session() as session:
            reap_summary = run_reaper(session)
            assert item_id in reap_summary["reclaimed"], f"Expected {item_id} in reclaimed, got {reap_summary}"

            # Verify row recovered cleanly
            recovered = session.query(OutboundQueue).filter_by(id=item_id).first()
            assert recovered.status == "eligible"
            assert recovered.locked_by is None
            assert recovered.lease_expires_at is None
            assert recovered.lease_expiry_count == 1
            assert recovered.attempts == 0

            # Assert reaper reclaim audit row
            reclaim_audit = (
                session.query(AuditLog)
                .filter_by(object_id=str(item_id), action="reaper_reclaim")
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert reclaim_audit is not None
            assert reclaim_audit.reason == AuditReason.LEASE_EXPIRED
            assert reclaim_audit.prev_state == "rendering"
            assert reclaim_audit.new_state == "eligible"

        # Assert no duplication: Worker 2 can now claim the reclaimed row
        with get_db_session() as session:
            claimed_by_w2 = claim_next_outbound_task(session, worker_id="worker_pid_9822")
            assert claimed_by_w2 is not None
            assert claimed_by_w2.id == item_id
            assert claimed_by_w2.locked_by == "worker_pid_9822"
            assert claimed_by_w2.status == "rendering"

            # Assert only 1 queue item exists in total (zero duplication)
            total_items = session.query(OutboundQueue).count()
            assert total_items == 1
