import pytest
from app.validators.sameness import (
    compute_trigram_jaccard,
    extract_opening_sentence,
    hash_sentence,
    validate_sameness,
)


def test_trigram_jaccard_identical_and_disjoint():
    text1 = "Narayani Distributors provides bulk spices and commodities across India."
    text2 = "Narayani Distributors provides bulk spices and commodities across India."
    assert compute_trigram_jaccard(text1, text2) == 1.0

    text3 = "Blue ocean waves crash against the sandy tropical beach."
    assert compute_trigram_jaccard(text1, text3) == 0.0


def test_prospect_history_similarity_threshold():
    prior_msg = """Hi Rajesh,

Narayani Distributors supplies premium agricultural products and bulk spices to distributors nationwide. Our facilities ensure complete quality control and testing across all export batches.

We would be glad to share our catalogue.

Best regards,
Aarav Shah"""

    # Highly similar draft (> 0.35 similarity)
    similar_draft = """Hi Rajesh,

Narayani Distributors supplies premium agricultural products and bulk spices to distributors nationwide. Our modern facilities ensure complete quality control and testing across all export batches.

We would be glad to share our complete catalogue.

Best regards,
Aarav Shah"""

    res_fail = validate_sameness(
        body=similar_draft,
        prospect_recent_messages=[prior_msg],
    )
    assert res_fail.passed is False
    assert res_fail.max_prospect_similarity >= 0.35
    assert any("Prospect similarity" in v for v in res_fail.violations)

    # Distinct draft (< 0.35 similarity)
    distinct_draft = """Hi Rajesh,

Following up on our agricultural distribution line, our Gujarat hub manages high-grade sesame and cumin processing with rigorous container packing.

Would you be open to reviewing our technical spec sheet?

Best regards,
Aarav Shah"""

    res_pass = validate_sameness(
        body=distinct_draft,
        prospect_recent_messages=[prior_msg],
    )
    assert res_pass.passed is True
    assert res_pass.max_prospect_similarity < 0.35


def test_segment_history_similarity_threshold():
    segment_msg = "Our warehouse dispatches fresh turmeric and cardamom lots to institutional kitchens in Maharashtra."
    # Almost identical text should exceed 0.50 threshold
    draft = "Our warehouse dispatches fresh turmeric and cardamom lots to institutional kitchens across Maharashtra."

    res_fail = validate_sameness(
        body=draft,
        segment_recent_messages=[segment_msg],
    )
    assert res_fail.passed is False
    assert res_fail.max_segment_similarity >= 0.50
    assert any("Segment similarity" in v for v in res_fail.violations)


def test_opening_sentence_hash_and_similarity():
    draft = "Hi Rajesh,\n\nFounded 40 years ago in Gujarat, Narayani Distributors leads spice exports.\n\nBest regards,\nAarav"
    opener = extract_opening_sentence(draft)
    assert opener == "Founded 40 years ago in Gujarat, Narayani Distributors leads spice exports."

    opener_hash = hash_sentence(opener)

    # 1. Exact hash collision
    res_hash_fail = validate_sameness(
        body=draft,
        opening_hashes=[opener_hash],
    )
    assert res_hash_fail.passed is False
    assert any("hash" in v for v in res_hash_fail.violations)

    # 2. High similarity on opening sentence (>= 0.60)
    prior_opener = "Founded 40 years ago in Gujarat, Narayani Distributors handles spice exports."
    res_sim_fail = validate_sameness(
        body=draft,
        recent_opening_sentences=[prior_opener],
    )
    assert res_sim_fail.passed is False
    assert res_sim_fail.max_opener_similarity >= 0.60
    assert any("Opening sentence similarity" in v for v in res_sim_fail.violations)


def test_phrases_to_avoid_and_banned_ledger():
    draft = "Hi Rajesh,\n\nWe provide agro supply. Let us explore synergy across our organizations.\n\nBest regards,\nAarav"
    res = validate_sameness(
        body=draft,
        phrases_to_avoid=["explore synergy"],
        banned_openers_ledger=["let us connect"],
    )
    assert res.passed is False
    assert any("explore synergy" in v for v in res.violations)
