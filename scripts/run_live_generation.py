import os
import sys
import json
from app.db.session import get_db_session
from app.workers.a2_personalization.service import run_a2_for_contact


def run_generation(contact_id: int):
    print("=" * 80)
    print(f"RUNNING LIVE GENERATION FOR CONTACT ID {contact_id}")
    print("=" * 80)

    with get_db_session() as db:
        final_state = run_a2_for_contact(
            contact_id=contact_id,
            db=db,
            include_notes=True,
            include_pii=False,
        )

    print("\n" + "#" * 30 + " GENERATION RESULTS " + "#" * 30)
    print(f"STATUS: {final_state.get('status')}")
    print(f"ATTEMPTS: {final_state.get('attempt')}")
    if final_state.get("hold_reason"):
        print(f"HOLD REASON: {final_state.get('hold_reason')}")
    if final_state.get("veto_reason"):
        print(f"VETO REASON: {final_state.get('veto_reason')}")

    print("\n--- VALIDATOR STATUS ---")
    print(f"Rulebook Errors: {final_state.get('rulebook_errors')}")
    print(f"Grounding Errors: {final_state.get('grounding_errors')}")
    print(f"Sameness Errors: {final_state.get('sameness_errors')}")

    meta = final_state.get("metadata")
    if meta:
        print("\n--- METADATA ---")
        print(f"Subject: {meta.subject}")
        print(f"Angle Used: {meta.angle_used}")
        print(f"CTA: {meta.cta}")
        print(f"Claims Used: {meta.claims_used}")
        print(f"Product Terms Used: {meta.product_terms_used}")
        print(f"Confidence: {meta.confidence}")
        print(f"Opening Hash: {meta.opening_line_hash}")
        print(f"Skip Reason: {meta.skip_reason}")

    print("\n--- GENERATED EMAIL BODY ---")
    print(final_state.get("draft_body", "<NO BODY GENERATED>"))
    print("=" * 80)
    return final_state


if __name__ == "__main__":
    target_id = 10
    if len(sys.argv) > 1:
        target_id = int(sys.argv[1])
    run_generation(target_id)
