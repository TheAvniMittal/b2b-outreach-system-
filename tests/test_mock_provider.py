import pytest
from app.config import settings
from app.db.session import get_db_session
from app.db.models import OutboundQueue, Contact, SendKey
from app.pipeline.providers.mock_provider import MockEmailProvider
from app.pipeline.runner import run_single_pipeline_item


def test_mock_provider_records_payload():
    mock = MockEmailProvider()
    res = mock.send_email(
        to_email="test@example.com",
        subject="Mock Subject",
        body_text="Mock Body",
        custom_headers={"X-Narayani-Send-Key": "sk_mock_999"},
    )
    assert res.success is True
    assert res.provider_message_id.startswith("mock_msg_")
    assert len(mock.sent_messages) == 1
    assert mock.sent_messages[0]["to_email"] == "test@example.com"
    assert mock.sent_messages[0]["custom_headers"]["X-Narayani-Send-Key"] == "sk_mock_999"


def test_end_to_end_pipeline_with_mock_provider(monkeypatch):
    """
    End-to-end pipeline test against MockEmailProvider:
    Queue -> claim -> A2 draft & validate -> mock send -> mark sent.
    Confirm TEST_MODE=true, LIVE_EMAIL_SENDING=false.
    """
    monkeypatch.setattr(settings, "test_mode", True)
    monkeypatch.setattr(settings, "live_email_sending", False)

    mock_provider = MockEmailProvider()

    with get_db_session() as db:
        # Create test contact & queue item for importer_india
        contact = Contact(
            company_name="Mock E2E Importer",
            contact_name="Rohan",
            email="avnimittal900+prospect3@gmail.com",
            segment="importer_india",
        )
        db.add(contact)
        db.commit()

        queue_item = OutboundQueue(
            contact_id=contact.id,
            status="eligible",
        )
        db.add(queue_item)
        db.commit()

        queue_id = queue_item.id

        # Mock run_a2_for_contact to return a clean emitted draft
        from app.schemas.a2_contract import MessageMetadata
        mock_meta = MessageMetadata(
            subject="Spice Supply Catalogue - Narayani",
            angle_used="compliance_first",
            cta="spec_sheet",
            claims_used=[1],
            product_terms_used=["spices"],
            confidence=0.9,
        )
        mock_a2_state = {
            "status": "emitted",
            "draft_body": "Hi Rohan,\n\nNarayani Distributors supplies certified bulk spices across India.\n\nBest regards,\nAarav",
            "metadata": mock_meta,
            "attempt": 1,
        }

        monkeypatch.setattr(
            "app.pipeline.runner.run_a2_for_contact",
            lambda *args, **kwargs: mock_a2_state,
        )

        res = run_single_pipeline_item(
            session=db,
            worker_id="test_worker_1",
            provider=mock_provider,
        )

        assert res is not None
        assert res["queue_item_id"] == queue_id
        assert res["status"] == "sent"
        assert len(mock_provider.sent_messages) == 1
        assert mock_provider.sent_messages[0]["to_email"] == "avnimittal900+prospect3@gmail.com"

        # Verify DB updated
        q_item = db.query(OutboundQueue).filter(OutboundQueue.id == queue_id).first()
        assert q_item.status == "sent"
        assert q_item.rendered_subject == "Spice Supply Catalogue - Narayani"

        sk_item = db.query(SendKey).filter(SendKey.queue_item_id == queue_id).first()
        assert sk_item is not None
        assert sk_item.status == "sent"
        assert sk_item.provider_message_id == res["provider_message_id"]
