import email
from email.message import EmailMessage
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Contact
from app.pipeline.imap_listener import (
    decode_header_str,
    extract_clean_email,
    strip_html_tags,
    extract_email_body,
    process_inbound_message,
    poll_imap_inbox,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_decode_header_and_clean_email():
    """Verifies that MIME encoded headers and email addresses are cleaned accurately."""
    # Clean email extraction
    assert extract_clean_email("John Doe <john@example.com>") == "john@example.com"
    assert extract_clean_email("ALEX@EXAMPLE.COM") == "alex@example.com"
    assert extract_clean_email('"Sales, Team" <sales.team@sub.domain.co.in>') == "sales.team@sub.domain.co.in"
    assert extract_clean_email(None) is None

    # Header decoding
    assert decode_header_str("Simple Subject") == "Simple Subject"
    assert decode_header_str("=?utf-8?B?VGVzdCBTdWJqZWN0?=") == "Test Subject"


def test_extract_email_body_multipart():
    """Verifies multipart plain text and HTML stripping."""
    msg = EmailMessage()
    msg["From"] = "prospect@example.com"
    msg["To"] = "mittalavni89@gmail.com"
    msg["Subject"] = "Re: Catalog inquiry"
    msg.set_content("Hello Narayani Team,\n\nWe would like to request your product specifications.")
    msg.add_alternative("<p>Hello Narayani Team,<br><br>We would like to request your product specifications.</p>", subtype="html")

    raw_bytes = msg.as_bytes()
    parsed = email.message_from_bytes(raw_bytes)
    extracted_body = extract_email_body(parsed)
    assert "We would like to request your product specifications" in extracted_body


def test_process_inbound_message_matches_contact():
    """Verifies that incoming email from a recognized contact runs A3 and updates contact."""
    with get_db_session() as session:
        # Find any contact in DB or create a test contact
        contact = session.query(Contact).first()
        assert contact is not None

        # Build mock inbound email from contact's email
        msg = EmailMessage()
        msg["From"] = f"{contact.contact_name} <{contact.email}>"
        msg["To"] = "mittalavni89@gmail.com"
        msg["Subject"] = "Inquiry regarding organic spices"
        msg.set_content("We are interested in your spice range. Please send us your catalogue and price sheet.")

        raw_bytes = msg.as_bytes()
        res = process_inbound_message(session=session, raw_message_bytes=raw_bytes)
        assert res["matched"] is True
        assert res["contact_id"] == contact.id
        assert "intent" in res


def test_process_inbound_message_unknown_sender():
    """Verifies that an incoming email from an unrecognized sender is safely ignored."""
    with get_db_session() as session:
        msg = EmailMessage()
        msg["From"] = "stranger.unknown.nobody@randomdomain12345.com"
        msg["To"] = "mittalavni89@gmail.com"
        msg["Subject"] = "Cold sales pitch"
        msg.set_content("Buy our services today.")

        raw_bytes = msg.as_bytes()
        res = process_inbound_message(session=session, raw_message_bytes=raw_bytes)
        assert res["matched"] is False
        assert "not found" in res["reason"].lower()


def test_inbound_status_and_poll_api(client):
    """Verifies GET /api/inbound/status and mocked POST /api/inbound/poll."""
    res = client.get("/api/inbound/status")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "configured" in data

    # Test mocked poll endpoint
    with patch("app.api.routers.management.poll_imap_inbox") as mock_poll:
        mock_poll.return_value = {
            "success": True,
            "total_unseen": 3,
            "checked": 3,
            "matched": 1,
            "results": [],
            "timestamp": "2026-10-28T12:00:00Z",
        }
        post_res = client.post("/api/inbound/poll?limit=10")
        assert post_res.status_code == 200
        poll_data = post_res.json()
        assert poll_data["success"] is True
        assert poll_data["total_unseen"] == 3
        assert poll_data["matched"] == 1
