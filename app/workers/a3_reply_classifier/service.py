from typing import Optional, Dict, Tuple
from sqlalchemy.orm import Session

from app.db.models import Contact
from app.schemas.a3_contract import A3ReplyContract
from app.workers.a3_reply_classifier.prefilter import run_prefilter
from app.workers.a3_reply_classifier.classifier import classify_reply_llm
from app.workers.a3_reply_classifier.overrides import apply_mechanical_overrides


def classify_inbound_reply(
    session: Session,
    contact: Contact,
    body: str,
    subject: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    segment: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Tuple[A3ReplyContract, dict]:
    """
    End-to-end A3 Reply Classification Pipeline (VISION.md §7.5):
    1. Deterministic pre-filter checks headers and OOO/bounce subject patterns (~70% auto-replies caught free).
    2. If pre-filter did not catch it: invokes LLM with quote-stripped, <untrusted_data>-fenced body.
    3. Runs mechanical overrides in code (suppression, confidence floor <0.6, human automation pause).
    4. Commits changes and returns (final_contract, actions_taken).
    """
    # 1. Deterministic pre-filter
    prefilter_result = run_prefilter(headers=headers, subject=subject)
    if prefilter_result is not None:
        final_contract, actions = apply_mechanical_overrides(session, contact, prefilter_result)
        actions["source"] = "prefilter"
        return final_contract, actions

    # 2. LLM Classifier
    from_domain = None
    if contact.email and "@" in contact.email:
        from_domain = contact.email.split("@")[1].lower().strip()

    headers_summary = None
    if headers:
        headers_summary = ", ".join(f"{k}: {v}" for k, v in list(headers.items())[:5])

    llm_result = classify_reply_llm(
        body=body,
        subject=subject,
        from_domain=from_domain,
        headers_summary=headers_summary,
        segment=segment or contact.segment,
        model_name=model_name,
    )

    # 3. Mechanical overrides
    final_contract, actions = apply_mechanical_overrides(session, contact, llm_result)
    actions["source"] = "llm"
    return final_contract, actions
