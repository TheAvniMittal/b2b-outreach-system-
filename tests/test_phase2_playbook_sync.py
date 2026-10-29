import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from app.main import app
from app.db.session import get_db_session
from app.db.models import Segment, RulebookConfig, AuditLog, AuditReason
from app.pipeline.playbook_service import parse_playbook_markdown


@pytest.fixture
def client():
    return TestClient(app)


def test_get_playbooks_endpoint(client):
    """Verifies that GET /api/playbooks returns all segments and global rulebook."""
    res = client.get("/api/playbooks")
    assert res.status_code == 200
    data = res.json()
    assert "segments" in data
    assert "global_rulebook" in data
    assert len(data["segments"]) >= 8

    # Verify key fields
    seg_names = [s["segment"] for s in data["segments"]]
    assert "distributor_wholesale" in seg_names
    assert "wellness_gym" in seg_names
    assert "fmcg_distributor" in seg_names

    dw = next(s for s in data["segments"] if s["segment"] == "distributor_wholesale")
    assert "target_words_min" in dw
    assert "target_words_max" in dw
    assert "allowed_angles" in dw
    assert "allowed_ctas" in dw


def test_put_segment_playbook_guarded_update(client):
    """Verifies guarded update of segment bounds, angles, and CTAs with audit trail."""
    # 1. Update distributor_wholesale bounds
    update_payload = {
        "target_words_min": 95,
        "target_words_max": 135,
        "allowed_angles": ["margin_opportunity", "territory_fit", "regional_reach"],
        "allowed_ctas": ["price_list", "catalogue", "call", "sample_box"],
        "forbidden_topics": ["FCL/container", "single_unit_retail_pricing", "consumer_pack"],
        "register_spec": "Updated direct B2B register for wholesale.",
        "operator_name": "lead_operator",
    }

    res = client.put("/api/playbooks/distributor_wholesale", json=update_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["segment"]["target_words_min"] == 95
    assert data["segment"]["target_words_max"] == 135
    assert "sample_box" in data["segment"]["allowed_ctas"]

    # 2. Verify DB persistence
    with get_db_session() as s:
        db_seg = s.query(Segment).filter(Segment.segment == "distributor_wholesale").first()
        assert db_seg.target_words_min == 95
        assert db_seg.target_words_max == 135
        assert "sample_box" in db_seg.allowed_ctas

        # Check Audit Log
        last_audit = s.query(AuditLog).filter(
            AuditLog.action == "update_segment_playbook",
            AuditLog.object_id == "distributor_wholesale"
        ).order_by(AuditLog.id.desc()).first()
        assert last_audit is not None
        assert last_audit.actor == "lead_operator"


def test_put_segment_playbook_validation_bounds(client):
    """Verifies that invalid bounds (min > max or negative) are rejected."""
    bad_payload = {
        "target_words_min": 150,
        "target_words_max": 90,
    }
    res = client.put("/api/playbooks/distributor_wholesale", json=bad_payload)
    assert res.status_code == 400
    assert "Target word minimum" in res.json()["detail"]


def test_put_nonexistent_segment_fails(client):
    """Verifies that attempting to update an unknown segment returns 400/404."""
    res = client.put("/api/playbooks/nonexistent_segment", json={"target_words_min": 80})
    assert res.status_code in [400, 404]


def test_export_playbook_markdown_endpoint(client):
    """Verifies that GET /api/playbooks/export-markdown produces a clean markdown document."""
    res = client.get("/api/playbooks/export-markdown")
    assert res.status_code == 200
    assert "text/markdown" in res.headers["content-type"]
    text = res.text
    assert "## 1. Segment → Catalogue mapping" in text
    assert "## 2. Per-segment playbooks" in text
    assert "### `distributor_wholesale`" in text
    assert "## 3. Dynamic Rulebook" in text


def test_import_playbook_markdown_endpoint(client):
    """Verifies that POST /api/playbooks/import-markdown parses markdown and updates DB."""
    test_md = """# Test Playbook
## 2. Per-segment playbooks
### `wellness_gym`
```yaml
segment: wellness_gym
target_words_min: 75
target_words_max: 105
allowed_angles: [nutrition_profile, clean_label, premium_gym_fit]
allowed_ctas: [sample_pack, nutrition_sheet, retail_pricing, consultation]
forbidden_topics: [FCL/container, import_documentation_language]
register_spec: Warm, fitness-focused tone.
```

## 3. Rulebook
```yaml
banned_openers:
  - "i hope this email finds you well"
  - "hope you are well"
banned_phrases:
  - "reaching out"
  - "leverage"
  - "revolutionary synergy"
max_paragraphs: 3
max_sentences_per_paragraph: 3
```
"""
    res = client.post(
        "/api/playbooks/import-markdown",
        json={"markdown_content": test_md, "operator_name": "import_tester"},
    )
    assert res.status_code == 200
    report = res.json()
    assert report["success"] is True
    assert "wellness_gym" in report["updated_segments"]
    assert report["rulebook_version"] is not None

    # Verify DB update for wellness_gym
    with get_db_session() as s:
        gym = s.query(Segment).filter(Segment.segment == "wellness_gym").first()
        assert gym.target_words_min == 75
        assert gym.target_words_max == 105
        assert "consultation" in gym.allowed_ctas

        # Verify rulebook updated
        rb = s.query(RulebookConfig).filter(RulebookConfig.version == report["rulebook_version"]).first()
        assert rb is not None
        assert "revolutionary synergy" in rb.banned_phrases


def test_import_broken_markdown_rejected(client):
    """Verifies that uploading invalid or empty markdown is rejected gracefully."""
    res = client.post(
        "/api/playbooks/import-markdown",
        json={"markdown_content": "Just a random text file with no playbook yaml blocks"},
    )
    assert res.status_code == 400
    assert "Could not find any valid segment playbooks" in res.json()["detail"]
