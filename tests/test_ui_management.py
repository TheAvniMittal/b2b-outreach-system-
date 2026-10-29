"""
Tests for UI Management & Read-Only Directory Endpoints.
Verifies all Phase 1 endpoints return correct status codes, structures, and privacy invariants.
"""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_contacts_endpoint():
    res = client.get("/api/contacts?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    if len(data["items"]) > 0:
        c = data["items"][0]
        assert "id" in c
        assert "contact_name" in c
        assert "company_name" in c
        assert "email" in c
        assert "segment" in c
        assert "verification_status" in c
        assert "status" in c


def test_queue_endpoint():
    res = client.get("/api/queue?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    if len(data["items"]) > 0:
        q = data["items"][0]
        assert "id" in q
        assert "contact_id" in q
        assert "contact_name" in q
        assert "company_name" in q
        assert "status" in q
        assert "attempts" in q


def test_suppression_endpoint_privacy():
    res = client.get("/api/suppression?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    for s in data["items"]:
        assert "id" in s
        assert "email_hash" in s
        assert "masked_email" in s
        # Ensure plaintext raw email is never exposed
        if "@" in s["masked_email"]:
            assert "***" in s["masked_email"]


def test_audit_log_endpoint():
    res = client.get("/api/audit-log?page=1&page_size=20")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "actors" in data
    assert "actions" in data
    assert "reasons" in data
    if len(data["items"]) > 0:
        a = data["items"][0]
        assert "id" in a
        assert "actor" in a
        assert "action" in a
        assert "object_type" in a
        assert "reason" in a


def test_system_config_endpoint():
    res = client.get("/api/system/config")
    assert res.status_code == 200
    data = res.json()
    assert "test_mode" in data
    assert "live_sending" in data
    assert "seed_sending_enabled" in data
    assert "smtp_host" in data
    assert "smtp_user" in data
    assert "allowlist_count" in data
    assert data["allowlist_count"] == 17
    assert "daily_cap" in data
    assert "active_segments_count" in data


def test_needs_review_items_endpoint():
    res = client.get("/api/review/items")
    assert res.status_code == 200
    data = res.json()
    assert "draft_holds" in data
    assert "contact_replies" in data
    assert "total_review_count" in data


def test_app_spa_serving():
    res = client.get("/app")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Narayani B2B Outreach" in res.text
    assert "chart.js" in res.text.lower()
    assert "view-overview" in res.text
    assert "view-review" in res.text
    assert "view-contacts" in res.text
    assert "view-queue" in res.text
    assert "view-suppression" in res.text
    assert "view-audit" in res.text
    assert "view-config" in res.text
    assert "draft-modal" in res.text
    assert "unsuppress-modal" in res.text
    assert "csv-modal" in res.text
    assert "modal-validation-errors" in res.text

