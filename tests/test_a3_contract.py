import pytest
from app.schemas.a3_contract import A3ReplyContract, A3Kind, A3Intent, A3SuppressionSignal


def test_contract_all_enums_have_unknown():
    assert "unknown" in [e.value for e in A3Kind]
    assert "unknown" in [e.value for e in A3Intent]
    assert "unknown" in [e.value for e in A3SuppressionSignal]


def test_contract_fallback_method():
    fallback = A3ReplyContract.fallback()
    assert fallback.kind == A3Kind.UNKNOWN
    assert fallback.intent == A3Intent.UNKNOWN
    assert fallback.suppression_signal == A3SuppressionSignal.UNKNOWN
    assert fallback.confidence == 0.0
    assert fallback.summary is not None
    assert fallback.evidence_quote is None


def test_contract_valid_construction():
    contract = A3ReplyContract(
        kind=A3Kind.HUMAN,
        intent=A3Intent.INTERESTED,
        suppression_signal=A3SuppressionSignal.NONE,
        confidence=0.95,
        summary="Customer wants product catalog",
        evidence_quote="Please send your catalog",
    )
    assert contract.kind == A3Kind.HUMAN
    assert contract.intent == A3Intent.INTERESTED
    assert contract.confidence == 0.95


def test_confidence_clamping():
    c_low = A3ReplyContract(
        kind=A3Kind.UNKNOWN,
        intent=A3Intent.UNKNOWN,
        confidence=-0.5,
    )
    assert c_low.confidence == 0.0

    c_high = A3ReplyContract(
        kind=A3Kind.UNKNOWN,
        intent=A3Intent.UNKNOWN,
        confidence=1.5,
    )
    assert c_high.confidence == 1.0
