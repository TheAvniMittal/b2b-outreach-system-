import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db_session
from app.db.models import Segment, Contact, Catalogue, ApprovedClaim
from app.workers.a2_personalization.brief_assembler import assemble_brief
from app.validators.rulebook_checks import validate_rulebook_v0


@pytest.fixture
def client():
    return TestClient(app)


def test_ui_renders_guarded_playbook_controls(client):
    """Verifies that index.html includes all Guarded Segment Playbooks & Markdown Sync Engine UI controls."""
    res = client.get("/app")
    assert res.status_code == 200
    html = res.text

    required_elements = [
        'id="card-guarded-playbooks"',
        'id="btn-export-playbook-md"',
        'id="btn-toggle-import-playbook"',
        'id="playbook-import-panel"',
        'id="playbook-md-file"',
        'id="playbook-md-textarea"',
        'id="btn-submit-import-playbook"',
        'id="playbook-segments-tabs"',
        'id="playbook-segment-editor"',
        'id="pb-seg-key"',
        'id="pb-min-words"',
        'id="pb-max-words"',
        'id="pb-allowed-angles"',
        'id="pb-allowed-ctas"',
        'id="pb-forbidden-topics"',
        'id="pb-register-spec"',
        'id="btn-save-segment-playbook"',
        'function fetchPlaybooks()',
        'function selectPlaybookSegment(',
        'function populatePlaybookEditor(',
    ]

    for elem in required_elements:
        assert elem in html, f"Missing UI element or function in index.html: {elem}"


def test_end_to_end_ui_save_syncs_brief_and_validator(client):
    """
    Verifies that when an operator updates segment playbook rules via the UI endpoint,
    assemble_brief() and validate_rulebook_v0() immediately enforce the new bounds.
    """
    # 1. Operator updates horeca_bakery via UI PUT endpoint to 70-115 words
    res = client.put(
        "/api/playbooks/horeca_bakery",
        json={
            "target_words_min": 70,
            "target_words_max": 115,
            "allowed_angles": ["bulk_formats", "batch_consistency", "chef_partnership"],
            "allowed_ctas": ["spec_sheet", "sample_pack", "call"],
            "forbidden_topics": ["single_unit_retail"],
            "register_spec": "Commercial bakery and executive chef register.",
            "operator_name": "ui_operator",
        },
    )
    assert res.status_code == 200

    # 2. Verify assemble_brief pulls the exact new bounds from DB
    with get_db_session() as s:
        seg = s.query(Segment).filter(Segment.segment == "horeca_bakery").first()
        cat = s.query(Catalogue).filter(Catalogue.id == seg.catalogue_id).first()
        claims = s.query(ApprovedClaim).filter(ApprovedClaim.segment == "horeca_bakery", ApprovedClaim.is_active == True).all()

        dummy_contact = {
            "company_name": "Artisan Bakers LLP",
            "contact_name": "Vikram Mehta",
            "country": "India",
            "city": "Mumbai",
        }

        brief = assemble_brief(
            contact=dummy_contact,
            segment=seg,
            catalogue=cat,
            approved_claims=claims,
        )

        assert brief.campaign_config["target_words_min"] == 70
        assert brief.campaign_config["target_words_max"] == 115
        assert "chef_partnership" in brief.campaign_config["allowed_angles"]

        # 3. Verify validator passes a 75-word draft under the new 70-115 bounds
        draft_75_words = (
            "Hi Vikram,\n\n"
            "Your commercial bakery operations in Mumbai align well with our bulk ingredient supply capabilities. "
            "We work closely with foodservice kitchens to maintain strict batch consistency across every production run.\n\n"
            "Our specialty flours, sugars, and bakery fillings are formulated for high-volume commercial kitchens and institutional baking schedules. "
            "Every batch undergoes rigorous quality checks to ensure reliable dough performance and uniform texture.\n\n"
            "We would be glad to share our bakery specification sheet with your team. "
            "Would you be open to reviewing our sample pack options this week?"
        )

        rb_res = validate_rulebook_v0(
            body=draft_75_words,
            first_name="Vikram",
            word_count_min=brief.campaign_config["target_words_min"],
            word_count_max=brief.campaign_config["target_words_max"],
        )
        assert rb_res.passed is True, f"Validator failed with new bounds: {rb_res.errors}"
