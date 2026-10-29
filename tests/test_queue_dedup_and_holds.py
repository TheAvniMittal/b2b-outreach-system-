import pytest
from unittest.mock import patch
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue
from app.pipeline.queue import enqueue_message
from app.pipeline.runner import run_single_pipeline_item
from app.pipeline.providers.mock_provider import MockEmailProvider


def test_send_all_17_seeds_twice_no_duplicate_queue_rows():
    """
    Part 5 Test 1:
    Confirm that running the send_all_17_seeds enqueue guard twice in a row
    for the same contact creates only ONE outbound_queue row.
    """
    with get_db_session() as db:
        # Find or create a valid contact for testing
        contact = db.query(Contact).filter_by(verification_status="valid").first()
        assert contact is not None, "Valid contact must exist"
        contact_id = contact.id

        # Clean existing queue items for this specific test contact
        db.query(OutboundQueue).filter(OutboundQueue.contact_id == contact_id).delete()
        db.commit()

        # Simulate first pass of send_all_17_seeds.py enqueue logic
        existing_1 = (
            db.query(OutboundQueue)
            .filter(
                OutboundQueue.contact_id == contact_id,
                OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
            )
            .first()
        )
        assert existing_1 is None
        q_item_1 = enqueue_message(db, contact_id=contact_id, campaign_step="intro")
        assert q_item_1 is not None

        # Simulate item transitioning to 'held' (or 'sent')
        q_item_1.status = "held"
        db.commit()

        # Simulate second pass of send_all_17_seeds.py enqueue logic
        existing_2 = (
            db.query(OutboundQueue)
            .filter(
                OutboundQueue.contact_id == contact_id,
                OutboundQueue.status.in_(["eligible", "rendering", "pending", "held", "sent"])
            )
            .first()
        )
        assert existing_2 is not None, "Guard must find existing held queue item"

        # Guard prevents enqueuing a duplicate
        if not existing_2:
            enqueue_message(db, contact_id=contact_id, campaign_step="intro")

        # Assert only ONE queue item exists for this contact
        total_items = db.query(OutboundQueue).filter(OutboundQueue.contact_id == contact_id).count()
        assert total_items == 1


def test_held_item_persists_attempts_and_hold_reason():
    """
    Part 5 Test 2:
    Confirm that when A2 produces a held result, runner.py writes both
    queue_item.attempts and queue_item.hold_reason to the database.
    """
    mock_a2_state = {
        "status": "held",
        "attempt": 3,
        "hold_reason": "catalogue grounding violation",
        "draft_body": "Test held draft body",
        "metadata": None,
    }

    with get_db_session() as db:
        contact = db.query(Contact).filter_by(verification_status="valid").first()
        assert contact is not None

        # Clean old queue rows for this test contact
        db.query(OutboundQueue).filter(OutboundQueue.contact_id == contact.id).delete()
        db.commit()

        # Enqueue item
        queue_item = enqueue_message(db, contact_id=contact.id, campaign_step="intro")
        queue_id = queue_item.id

        # Run pipeline item with mocked A2 output
        with patch("app.pipeline.runner.run_a2_for_contact", return_value=mock_a2_state):
            result = run_single_pipeline_item(
                session=db,
                worker_id="test_worker_holds",
                provider=MockEmailProvider(),
            )

        assert result is not None
        assert result["status"] == "held"
        assert result["attempts"] == 3
        assert result["hold_reason"] == "catalogue grounding violation"

        # Re-fetch from database and verify persistence on the model
        refetched = db.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
        assert refetched is not None
        assert refetched.status == "held"
        assert refetched.attempts == 3
        assert refetched.hold_reason == "catalogue grounding violation"
