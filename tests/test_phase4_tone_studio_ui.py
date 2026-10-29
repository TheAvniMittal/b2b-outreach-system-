"""
Phase 4 Tone Studio UI & Operator Screens Tests (§A).
Verifies:
1. UI HTML (/app, /ui) serves the complete Tone Studio screen and components.
2. Sidebar navigation includes Tone Studio.
3. Plain-language validator checklist and applied tone card are present.
4. Quick tone buttons (5 default tone buttons) and custom input exist.
5. Operator name tracking input and confirmation modals (approve, reject, rollback) exist.
6. Daily usage counter ("X of 10 used today, resets at 00:00 UTC") is present.
7. "What the Email Writer will receive" panel and Version History table exist.
8. API endpoints cleanly power the UI views.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session

client = TestClient(app)


def test_ui_serves_tone_studio_screen_and_navigation():
    """Verify that /app serves index.html containing Tone Studio navigation and screen."""
    res = client.get("/app")
    assert res.status_code == 200
    html = res.text

    # Sidebar Navigation item
    assert 'data-tab="tonestudio"' in html
    assert "Tone Studio" in html

    # Main view container
    assert 'id="view-tonestudio"' in html
    assert 'id="tone-segments-tabs-container"' in html
    assert 'id="tone-usage-counter"' in html

    # 5 Quick Tone Buttons
    assert 'data-tone="Warmer"' in html
    assert 'data-tone="More formal"' in html
    assert 'data-tone="More direct"' in html
    assert 'data-tone="Less salesy"' in html
    assert 'data-tone="Simpler words"' in html

    # Custom prompt input and operator name
    assert 'id="tone-custom-input"' in html
    assert 'id="tone-operator-name"' in html
    assert 'id="btn-generate-tone-sample"' in html

    # Draft display and plain-language validator checklist
    assert 'id="tone-draft-body"' in html
    assert 'id="tone-notes-display"' in html
    assert 'id="tone-val-checklist"' in html
    assert "Plain-Language Validator Checklist" in html

    # Action buttons
    assert 'id="btn-approve-tone-sample"' in html
    assert 'id="btn-reject-tone-sample"' in html

    # Panels: Writer Preview & Version History
    assert "What the Email Writer Will Receive" in html
    assert 'id="writer-preview-version"' in html
    assert 'id="writer-preview-notes"' in html
    assert 'id="writer-preview-excerpt"' in html
    assert "Version History & Rollback" in html
    assert 'id="tone-history-tbody"' in html

    # Modals
    assert 'id="tone-approve-modal"' in html
    assert 'id="tone-reject-modal"' in html
    assert 'id="tone-rollback-modal"' in html


def test_api_powers_tone_studio_ui():
    """Verify that backend endpoints supply all data required by the UI."""
    # 1. Segments list
    res_segs = client.get("/api/tone-studio/segments")
    assert res_segs.status_code == 200
    segs_data = res_segs.json()["segments"]
    assert len(segs_data) > 0

    first_seg = segs_data[0]["segment"]

    # 2. Segment chat and workspace details
    res_chat = client.get(f"/api/tone-studio/{first_seg}/chat")
    assert res_chat.status_code == 200
    chat_data = res_chat.json()

    assert "synthetic_contact" in chat_data
    assert chat_data["synthetic_contact"]["is_synthetic"] is True
    assert "writer_preview" in chat_data
    assert "quick_prompts" in chat_data
    assert len(chat_data["quick_prompts"]) == 5
    assert "usage" in chat_data
    assert chat_data["usage"]["limit"] == 10
