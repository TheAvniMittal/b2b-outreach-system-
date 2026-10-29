import pytest
from app.db.session import get_db_session
from app.db.models import OutboundQueue, Contact, SendKey
from app.pipeline.providers.base import EmailProvider, ProviderSendResult
from app.pipeline.sender import execute_send


class CrashingProvider(EmailProvider):
    def __init__(self, check_callback):
        self.check_callback = check_callback

    def send_email(self, to_email, subject, body_text, body_html=None, custom_headers=None, session=None, contact_id=None):
        # When provider is called, execute callback to inspect database state
        self.check_callback()
        raise ConnectionResetError("Simulated network crash during provider call!")


class SuccessfulProvider(EmailProvider):
    def send_email(self, to_email, subject, body_text, body_html=None, custom_headers=None, session=None, contact_id=None):
        return ProviderSendResult(
            success=True,
            provider_message_id="msg_real_success_456",
        )


def test_send_key_is_committed_before_provider_call():
    """Confirm send_key is written and committed with status='sending' BEFORE provider call (§14.1d)."""
    with get_db_session() as db:
        contact = Contact(
            company_name="PreCommit Test Corp",
            contact_name="Ramesh",
            email="avnimittal900+prospect2@gmail.com",
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

        queue_item_id = queue_item.id
        contact_id = contact.id

        def verify_db_state_during_call():
            # Open a completely separate independent database connection to verify pre-commit!
            with get_db_session() as independent_db:
                sk = independent_db.query(SendKey).filter(SendKey.queue_item_id == queue_item_id).first()
                assert sk is not None, "FATAL: send_key was NOT committed before provider call!"
                assert sk.status == "sending", f"Expected status 'sending', got '{sk.status}'"
                assert sk.contact_id == contact_id

        crashing_provider = CrashingProvider(check_callback=verify_db_state_during_call)

        with pytest.raises(ConnectionResetError):
            execute_send(
                session=db,
                queue_item_id=queue_item_id,
                subject="Test Subject",
                body_text="Test Body",
                provider=crashing_provider,
            )

        # After crash: verify send_key remains committed with status='sending' for reaper / crash reconciliation
        with get_db_session() as independent_db:
            sk_after = independent_db.query(SendKey).filter(SendKey.queue_item_id == queue_item_id).first()
            assert sk_after is not None
            assert sk_after.status == "sending"

        # Now test successful completion
        success_provider = SuccessfulProvider()
        sk_success, res = execute_send(
            session=db,
            queue_item_id=queue_item_id,
            subject="Test Subject Success",
            body_text="Test Body Success",
            provider=success_provider,
        )
        assert res.success is True
        assert sk_success.status == "sent"
        assert sk_success.provider_message_id == "msg_real_success_456"

        q_after = db.query(OutboundQueue).filter(OutboundQueue.id == queue_item_id).first()
        assert q_after.status == "sent"
