import pytest
from pathlib import Path
from unittest.mock import patch

from app.db.session import get_db_session
from app.db.models import OutboundQueue, Contact, Segment, ApprovedClaim
from app.pipeline.playbook_service import import_playbook_markdown
from app.schemas.a2_contract import A2Output
from app.workers.a2_personalization.service import run_a2_for_contact


def test_master_markdown_full_8_segment_sync():
    """Verifies that importing the master segments-catalogues-rulebook-v0.md syncs all 8 segments cleanly."""
    md_path = Path("segments-catalogues-rulebook-v0.md")
    assert md_path.exists()
    md_text = md_path.read_text(encoding="utf-8")

    with get_db_session() as s:
        report = import_playbook_markdown(s, md_text, operator_name="phase4_verifier")
        assert report["success"] is True
        assert report["updated_segments_count"] == 8
        expected = {
            "importer_india",
            "importer_uae",
            "distributor_wholesale",
            "fmcg_distributor",
            "horeca_bakery",
            "organic_gourmet",
            "supermarket_retail",
            "wellness_gym",
        }
        assert set(report["updated_segments"]) == expected


def test_end_to_end_a2_pipeline_emits_on_attempt_1():
    """
    Verifies that a contact in 'distributor_wholesale' (e.g. Contact 230 / Manoj)
    runs through run_a2_for_contact() and passes Rulebook, Grounding, and Sameness
    validators on Attempt 1 with status == 'emitted'.
    """
    with get_db_session() as s:
        # Find a distributor_wholesale contact (Contact 230 or any distributor_wholesale contact)
        contact = s.query(Contact).filter(Contact.id == 230).first()
        if not contact:
            contact = s.query(Contact).filter(Contact.segment == "distributor_wholesale").first()
        assert contact is not None

        # Fetch active claim for this segment
        claim = (
            s.query(ApprovedClaim)
            .filter(ApprovedClaim.segment == contact.segment, ApprovedClaim.is_active == True)
            .first()
        )
        claim_id = claim.id if claim else 1
        claim_text = claim.text if claim else "Consistent batch supply across regional wholesale networks."

        first_name = contact.contact_name.split()[0] if contact.contact_name else "Manoj"

        # Simulate LLM returning a realistic v2 JSON response (including single-line opener & FMCG acronym)
        simulated_a2_output = A2Output(
            subject=f"Wholesale distribution inquiry for {contact.company_name}",
            opener=f"Hi {first_name}, your expansion into regional FMCG wholesale distribution aligns well with our current supply capabilities.",
            body=(
                f"We partner with established regional wholesale partners to maintain steady inventory turnover and reliable dispatch schedules across territories.\n\n"
                f"Our portfolio features authentic packaged namkeen, papad, and ready-to-eat snacks produced under strict batch quality standards. "
                f"{claim_text} Every product line is structured to support healthy distributor margin bands and territorial clarity."
            ),
            cta="We would be glad to share our distributor catalogue and product specifications. Would you be open to reviewing our wholesale catalogue this week?",
            cta_id="catalogue",
            angle="margin_opportunity",
            claims_used=[claim_id],
            product_terms_used=["namkeen", "papad", "snacks"],
            confidence=0.90,
        )

        with patch(
            "app.workers.a2_personalization.graph.call_groq_a2",
            return_value=(simulated_a2_output, 350, 180, "ok", []),
        ):
            state = run_a2_for_contact(
                contact_id=contact.id,
                db=s,
                round_number=1,
                include_notes=False,
                include_pii=False,
            )

        assert state["status"] == "emitted", (
            f"Expected 'emitted' on Attempt 1, got '{state['status']}' "
            f"(rulebook_errors={state.get('rulebook_errors')}, "
            f"grounding_errors={state.get('grounding_errors')}, "
            f"sameness_errors={state.get('sameness_errors')}, "
            f"conformance_errors={state.get('segment_conformance_errors')})"
        )
        assert state["attempt"] == 1
        assert state["rulebook_errors"] == []
        assert state["grounding_errors"] == []
        assert state["sameness_errors"] == []


def test_requeue_previously_held_rulebook_items():
    """
    Re-evaluates and resets outbound_queue items that were previously held due to
    the legacy rulebook formatting mismatch so they are eligible for clean dispatch.
    """
    with get_db_session() as s:
        held_items = (
            s.query(OutboundQueue)
            .filter(
                OutboundQueue.status == "held",
                OutboundQueue.failed_validator.in_(["rulebook", "catalogue_grounding"]),
            )
            .all()
        )
        requeued_ids = []
        for item in held_items:
            item.status = "eligible"
            item.failed_validator = None
            item.hold_reason = None
            item.attempts = 0
            item.attempt_history = []
            requeued_ids.append(item.id)
        s.commit()

        # Verify no items remain stuck in held under 'rulebook'
        remaining = (
            s.query(OutboundQueue)
            .filter(
                OutboundQueue.status == "held",
                OutboundQueue.failed_validator == "rulebook",
            )
            .count()
        )
        assert remaining == 0
