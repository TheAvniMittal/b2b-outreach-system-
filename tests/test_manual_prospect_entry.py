import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact, OutboundQueue, Suppression
from app.pipeline.import_csv import hash_email

client = TestClient(app)


def test_manual_prospect_creation_and_auto_enqueue():
    """Verify manual prospect entry creates contact and enqueues into OutboundQueue."""
    test_email = "farooq.lead@gulfood-test-uae.com"
    
    # Cleanup if exists from previous run
    with get_db_session() as db:
        existing = db.query(Contact).filter_by(email=test_email).first()
        if existing:
            db.query(OutboundQueue).filter_by(contact_id=existing.id).delete()
            db.delete(existing)
            db.commit()

    payload = {
        "company_name": "Al Barakah Food Trading LLC",
        "contact_name": "Farooq Al-Mansoor",
        "email": test_email,
        "segment": "importer_uae",
        "designation": "Head of Sourcing",
        "company_domain": "gulfood-test-uae.com",
        "country": "UAE",
        "product_interest": "Organic Cumin Seeds, Turmeric Fingers",
        "notes": "Met at Gulfood 2026. Needs private label pricing.",
        "auto_enqueue": True,
    }

    res = client.post("/api/contacts/manual", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["success"] is True
    assert data["contact_name"] == "Farooq Al-Mansoor"
    assert data["email"] == test_email
    assert data["segment"] == "importer_uae"
    assert data["enqueued"] is True
    assert data["queue_item_id"] is not None

    # Verify database state
    with get_db_session() as db:
        c = db.query(Contact).filter_by(email=test_email).first()
        assert c is not None
        assert c.company_name == "Al Barakah Food Trading LLC"
        assert c.product_interest == "Organic Cumin Seeds, Turmeric Fingers"
        assert c.notes == "Met at Gulfood 2026. Needs private label pricing."

        q = db.query(OutboundQueue).filter_by(id=data["queue_item_id"]).first()
        assert q is not None
        assert q.contact_id == c.id
        assert q.status == "eligible"
        assert q.campaign_step == "intro"


def test_manual_prospect_creation_without_auto_enqueue():
    """Verify manual prospect entry with auto_enqueue=False does not create queue task."""
    test_email = "omar.lead@uae-wholesale-demo.com"

    with get_db_session() as db:
        existing = db.query(Contact).filter_by(email=test_email).first()
        if existing:
            db.query(OutboundQueue).filter_by(contact_id=existing.id).delete()
            db.delete(existing)
            db.commit()

    payload = {
        "company_name": "Emirates Spices & Grains",
        "contact_name": "Omar Tariq",
        "email": test_email,
        "segment": "distributor_wholesale",
        "designation": "Purchasing Lead",
        "auto_enqueue": False,
    }

    res = client.post("/api/contacts/manual", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["success"] is True
    assert data["enqueued"] is False
    assert data["queue_item_id"] is None

    with get_db_session() as db:
        c = db.query(Contact).filter_by(email=test_email).first()
        assert c is not None
        q = db.query(OutboundQueue).filter_by(contact_id=c.id).first()
        assert q is None


def test_manual_prospect_invalid_segment_rejected():
    """Verify unknown segment triggers 400 with helpful error."""
    payload = {
        "company_name": "Invalid Seg Corp",
        "contact_name": "Alice Smith",
        "email": "alice@invalid-segment-test.com",
        "segment": "completely_fictional_segment",
        "auto_enqueue": True,
    }

    res = client.post("/api/contacts/manual", json=payload)
    assert res.status_code == 400
    assert "Unknown segment 'completely_fictional_segment'" in res.json()["detail"]


def test_manual_prospect_invalid_email_rejected():
    """Verify malformed email triggers 400 error."""
    payload = {
        "company_name": "Bad Email Corp",
        "contact_name": "Bob Smith",
        "email": "not-a-valid-email-string",
        "segment": "importer_uae",
    }

    res = client.post("/api/contacts/manual", json=payload)
    assert res.status_code == 400
    assert "Invalid email address format" in res.json()["detail"]


def test_manual_prospect_suppression_refuses_with_clear_message():
    """Verify email on suppression list is strictly refused with 400 Bad Request and clear error."""
    test_email = "optout.lead@suppressed-domain-test.com"
    e_hash = hash_email(test_email)

    with get_db_session() as db:
        # Ensure suppression entry exists
        supp = db.query(Suppression).filter_by(email_hash=e_hash).first()
        if not supp:
            db.add(Suppression(email_hash=e_hash, reason="unsubscribed"))
            db.commit()

        # Clean existing contact if any
        existing = db.query(Contact).filter_by(email=test_email).first()
        if existing:
            db.query(OutboundQueue).filter_by(contact_id=existing.id).delete()
            db.delete(existing)
            db.commit()

    payload = {
        "company_name": "Opted Out LLC",
        "contact_name": "Karen Optout",
        "email": test_email,
        "segment": "supermarket_retail",
        "auto_enqueue": True,
    }

    res = client.post("/api/contacts/manual", json=payload)
    assert res.status_code == 400
    err_detail = res.json()["detail"]
    assert "Refused" in err_detail
    assert "Suppression List" in err_detail
    assert test_email in err_detail

    # Verify contact was NOT created and NOT enqueued
    with get_db_session() as db:
        c = db.query(Contact).filter_by(email=test_email).first()
        assert c is None


def test_manual_prospect_update_existing_contact_without_duplicate_queue():
    """Verify re-submitting an existing email updates details and does not duplicate active queue task."""
    test_email = "reentry.lead@spice-traders.com"

    with get_db_session() as db:
        existing = db.query(Contact).filter_by(email=test_email).first()
        if existing:
            db.query(OutboundQueue).filter_by(contact_id=existing.id).delete()
            db.delete(existing)
            db.commit()

    # Step 1: Initial creation
    res1 = client.post("/api/contacts/manual", json={
        "company_name": "Original Company Name",
        "contact_name": "Original Contact Name",
        "email": test_email,
        "segment": "importer_uae",
        "auto_enqueue": True,
    })
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["enqueued"] is True
    first_q_id = d1["queue_item_id"]

    # Step 2: Re-entry with updated details
    res2 = client.post("/api/contacts/manual", json={
        "company_name": "Updated Company Name LLC",
        "contact_name": "Updated Contact Name",
        "email": test_email,
        "segment": "importer_uae",
        "product_interest": "Organic Fenugreek Seeds",
        "auto_enqueue": True,
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["contact_id"] == d1["contact_id"]
    assert d2["enqueued"] is False  # already in queue, so not duplicate enqueued

    with get_db_session() as db:
        c = db.query(Contact).filter_by(id=d1["contact_id"]).first()
        assert c.company_name == "Updated Company Name LLC"
        assert c.contact_name == "Updated Contact Name"
        assert c.product_interest == "Organic Fenugreek Seeds"

        # Verify only 1 queue record exists
        q_items = db.query(OutboundQueue).filter_by(contact_id=c.id).all()
        assert len(q_items) == 1
        assert q_items[0].id == first_q_id
