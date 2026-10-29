import io
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression, AuditLog, AuditReason
from app.pipeline.import_csv import import_prospects_csv, hash_email

client = TestClient(app)

TEST_SEGMENT = "horeca_bakery"


def clean_test_data(emails):
    with get_db_session() as session:
        for email in emails:
            e_hash = hash_email(email)
            session.query(Suppression).filter(Suppression.email_hash == e_hash).delete(synchronize_session=False)
            contacts = session.query(Contact).filter(Contact.email == email).all()
            for c in contacts:
                session.query(AuditLog).filter(AuditLog.object_id == str(c.id)).delete(synchronize_session=False)
                queue_items = session.query(OutboundQueue).filter(OutboundQueue.contact_id == c.id).all()
                for q in queue_items:
                    session.query(AuditLog).filter(AuditLog.object_id == str(q.id)).delete(synchronize_session=False)
                session.query(OutboundQueue).filter(OutboundQueue.contact_id == c.id).delete(synchronize_session=False)
                session.delete(c)
        session.commit()


def test_csv_import_auto_enqueues():
    test_email = "test_phase4_auto_enq@example.com"
    clean_test_data([test_email])

    try:
        csv_data = f"""company_name,contact_name,email,segment,country
Phase4 Auto Enq Co,John Baker,{test_email},{TEST_SEGMENT},United States
"""
        res = client.post(
            "/api/contacts/import-csv",
            content=csv_data.encode("utf-8"),
            headers={"Content-Type": "text/csv"}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["success"] is True
        assert data["imported_count"] == 1
        assert data["enqueued_count"] == 1

        with get_db_session() as session:
            contact = session.query(Contact).filter(Contact.email == test_email).first()
            assert contact is not None
            assert contact.status == "pending"

            q_item = session.query(OutboundQueue).filter(OutboundQueue.contact_id == contact.id).first()
            assert q_item is not None
            assert q_item.status == "eligible"
            assert q_item.campaign_step == "intro"

            # Check audit log
            audit = session.query(AuditLog).filter(
                AuditLog.object_type == "queue_item",
                AuditLog.object_id == str(q_item.id),
                AuditLog.reason == AuditReason.INITIAL_ENQUEUE
            ).first()
            assert audit is not None
    finally:
        clean_test_data([test_email])


def test_enqueue_pending_endpoint_idempotency():
    test_email = "test_phase4_idempotent@example.com"
    clean_test_data([test_email])

    try:
        # Create a pending contact directly without queue item
        with get_db_session() as session:
            contact = Contact(
                company_name="Idempotent Test Corp",
                contact_name="Alice Smith",
                email=test_email,
                segment=TEST_SEGMENT,
                status="pending",
                verification_status="valid",
            )
            session.add(contact)
            session.commit()
            cid = contact.id

        # First run: should enqueue
        res1 = client.post("/api/queue/enqueue-pending")
        assert res1.status_code == 200, res1.text
        data1 = res1.json()
        assert data1["success"] is True
        assert data1["enqueued_count"] >= 1

        with get_db_session() as session:
            q_created = session.query(OutboundQueue).filter(OutboundQueue.contact_id == cid).first()
            assert q_created is not None
            created_qid = q_created.id
            assert created_qid in data1["enqueued_ids"]

        # Second run: should skip, not re-enqueue
        res2 = client.post("/api/queue/enqueue-pending")
        assert res2.status_code == 200, res2.text
        data2 = res2.json()
        assert data2["success"] is True
        assert created_qid not in data2["enqueued_ids"]
        assert data2["enqueued_count"] == 0

        # Verify only 1 queue item exists
        with get_db_session() as session:
            items = session.query(OutboundQueue).filter(OutboundQueue.contact_id == cid).all()
            assert len(items) == 1
            assert items[0].status == "eligible"
    finally:
        clean_test_data([test_email])


def test_suppressed_contact_excluded_from_enqueue():
    test_email = "test_phase4_suppressed@example.com"
    clean_test_data([test_email])

    try:
        e_hash = hash_email(test_email)
        with get_db_session() as session:
            supp = Suppression(
                email_hash=e_hash,
                reason="unsubscribe_request",
                created_by="operator"
            )
            session.add(supp)
            contact = Contact(
                company_name="Suppressed Test Corp",
                contact_name="Bob Brown",
                email=test_email,
                segment=TEST_SEGMENT,
                status="suppressed",
                verification_status="valid",
            )
            session.add(contact)
            session.commit()
            cid = contact.id

        # Run enqueue-pending
        res = client.post("/api/queue/enqueue-pending")
        assert res.status_code == 200, res.text
        data = res.json()
        assert cid not in data["enqueued_ids"]

        # Confirm no OutboundQueue item created
        with get_db_session() as session:
            q_count = session.query(OutboundQueue).filter(OutboundQueue.contact_id == cid).count()
            assert q_count == 0
    finally:
        clean_test_data([test_email])


def test_invalid_contact_excluded_from_enqueue():
    test_email = "test_phase4_invalid@example.com"
    clean_test_data([test_email])

    try:
        with get_db_session() as session:
            contact = Contact(
                company_name="Invalid Test Corp",
                contact_name="Charlie Red",
                email=test_email,
                segment=TEST_SEGMENT,
                status="held",
                verification_status="invalid",
            )
            session.add(contact)
            session.commit()
            cid = contact.id

        res = client.post("/api/queue/enqueue-pending")
        assert res.status_code == 200, res.text
        data = res.json()
        assert cid not in data["enqueued_ids"]

        with get_db_session() as session:
            q_count = session.query(OutboundQueue).filter(OutboundQueue.contact_id == cid).count()
            assert q_count == 0
    finally:
        clean_test_data([test_email])
