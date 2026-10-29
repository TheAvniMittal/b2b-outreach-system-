import pytest
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.schemas.a3_contract import A3ReplyContract, A3Kind, A3Intent, A3SuppressionSignal
from app.workers.a3_reply_classifier.overrides import apply_mechanical_overrides, hash_email
from app.pipeline.queue import enqueue_message


@pytest.fixture
def mock_contact():
    with get_db_session() as session:
        contact = Contact(
            company_name="Test Override LLC",
            segment="importer_india",
            contact_name="Alice Smith",
            email="alice.override.test@example.com",
            verification_status="valid",
            status="active",
            retention_class="prospect_pii",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    yield contact_id

    # Clean up
    with get_db_session() as session:
        session.query(OutboundQueue).filter_by(contact_id=contact_id).delete()
        c = session.query(Contact).filter_by(id=contact_id).first()
        if c:
            session.query(Suppression).filter_by(email_hash=hash_email(c.email)).delete()
            session.delete(c)
        session.commit()


def test_suppression_override_fires_regardless_of_model_output(mock_contact):
    """
    VISION.md §7.5:
    'if suppression_signal != "none": suppress(contact) # regardless of anything else'
    Even if the model hallucinated kind='human' and intent='interested',
    a suppression signal must unconditionally suppress the contact!
    """
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=mock_contact).first()
        enqueue_message(session, contact_id=contact.id, campaign_step="intro")

        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.INTERESTED,  # model said interested!
            suppression_signal=A3SuppressionSignal.UNSUBSCRIBE_REQUEST,
            confidence=0.99,
        )

        final_contract, actions = apply_mechanical_overrides(session, contact, contract)

        assert actions["suppressed"] is True
        assert contact.status == "suppressed"

        # Check suppression table entry exists with email hash
        e_hash = hash_email(contact.email)
        sup = session.query(Suppression).filter_by(email_hash=e_hash).first()
        assert sup is not None
        assert sup.reason == "unsubscribe_request"

        # Check in-flight queue items were cancelled
        queue_items = session.query(OutboundQueue).filter_by(contact_id=contact.id).all()
        for q in queue_items:
            assert q.status == "cancelled"

        # Check audit log
        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(contact.id), action="suppress")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.UNSUBSCRIBE_REQUEST


def test_confidence_below_point_six_forces_unknown_and_routes_to_human(mock_contact):
    """
    VISION.md §7.5:
    'if confidence < 0.6: kind = "unknown"; route_to_human()'
    """
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=mock_contact).first()

        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.INTERESTED,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=0.55,  # below 0.6 threshold!
        )

        final_contract, actions = apply_mechanical_overrides(session, contact, contract)

        assert final_contract.kind == A3Kind.UNKNOWN
        assert actions["final_kind"] == A3Kind.UNKNOWN
        assert actions["routed_to_human"] is True
        assert contact.status == "held"

        # Verify audit log
        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(contact.id), action="route_to_human")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.VALIDATOR_HOLD


def test_human_reply_pauses_automation(mock_contact):
    """
    VISION.md §7.5:
    'if kind == "human": pause_automation(prospect)'
    """
    with get_db_session() as session:
        contact = session.query(Contact).filter_by(id=mock_contact).first()
        enqueue_message(session, contact_id=contact.id, campaign_step="followup_1")

        contract = A3ReplyContract(
            kind=A3Kind.HUMAN,
            intent=A3Intent.WANTS_CATALOGUE,
            suppression_signal=A3SuppressionSignal.NONE,
            confidence=0.85,
        )

        final_contract, actions = apply_mechanical_overrides(session, contact, contract)

        assert actions["automation_paused"] is True
        assert contact.status == "held"

        queue_items = session.query(OutboundQueue).filter_by(contact_id=contact.id).all()
        for q in queue_items:
            assert q.status == "held"

        audit = (
            session.query(AuditLog)
            .filter_by(object_id=str(contact.id), action="pause_automation")
            .first()
        )
        assert audit is not None
        assert audit.reason == AuditReason.HUMAN_REPLY
