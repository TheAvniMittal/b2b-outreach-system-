import pytest
from app.schemas.a2_contract import A2Output
from app.validators.rulebook_checks import validate_rulebook_v0
from app.workers.a2_personalization.graph import A2_SYSTEM_PROMPT_V2


def test_sentence_chunking_balanced_under_all_sentence_counts():
    """Verifies that assemble_prose_for_validation never creates a paragraph with > 3 sentences."""
    # Test across varying sentence counts from 1 to 12
    for n in range(1, 13):
        sentences = [f"Sentence number {i} is here." for i in range(1, n + 1)]
        out = A2Output(
            subject="Test subject",
            opener=f"Hi Alex, {sentences[0]}",
            body="\n\n".join(sentences[1:-1]) if n > 2 else (sentences[1] if n == 2 else ""),
            cta=sentences[-1] if n > 1 else "Would you like a catalogue?",
            cta_id="spec_sheet",
            angle="compliance_first",
            claims_used=[4],
            product_terms_used=["organic spices"],
            confidence=0.9,
        )
        prose = out.assemble_prose_for_validation()
        paragraphs = [p.strip() for p in prose.split("\n\n") if p.strip() and not p.startswith("Hi ")]
        
        # Verify paragraph count <= 3
        assert len(paragraphs) <= 3, f"Expected <= 3 paragraphs for n={n}, got {len(paragraphs)}"
        
        # Verify each paragraph sentence count
        for idx, p in enumerate(paragraphs, 1):
            s_list = [s for s in p.split(".") if s.strip()]
            assert len(s_list) <= 3, f"For n={n}, paragraph {idx} had {len(s_list)} sentences (expected <= 3): '{p}'"
            if len(paragraphs) > 1:
                assert len(s_list) >= 2, f"For n={n}, paragraph {idx} had {len(s_list)} sentences (expected >= 2): '{p}'"


def test_queue_481_and_482_cases_now_pass_sentence_validation():
    """
    Specifically reproduces the 9-sentence case from Queue 481 and 482
    and verifies it produces 3 paragraphs of exactly 3 sentences each.
    """
    sentences = [
        "I am writing to introduce our export capabilities tailored for the UAE market.",
        "We understand the strict documentation standards required for your region.",
        "Our spice range is HALAL certified specifically for GCC markets.",
        "We ensure all blends are packed to meet local labelling requirements.",
        "This approach simplifies your customs clearance process significantly.",
        "Additionally, our dry fruit range includes almonds, cashews, and dates sourced for the UAE trade.",
        "Our export facility holds GMP certification to guarantee consistent quality.",
        "Would you like to review our current certifications pack and product specifications?",
        "We can also arrange a sample shipment for your evaluation.",
    ]
    assert len(sentences) == 9

    out = A2Output(
        subject="HALAL certified spices and GCC documentation for Lifco",
        opener=f"Hi Trade, {sentences[0]} {sentences[1]}",
        body=f"{sentences[2]} {sentences[3]} {sentences[4]}\n\n{sentences[5]} {sentences[6]}",
        cta=f"{sentences[7]} {sentences[8]}",
        cta_id="spec_sheet",
        angle="compliance_first",
        claims_used=[4],
        product_terms_used=["organic spices"],
        confidence=0.95,
    )
    prose = out.assemble_prose_for_validation()
    res = validate_rulebook_v0(prose, first_name="Trade", word_count_min=50, word_count_max=150)
    assert res.passed is True, f"Expected prose to pass rulebook validation, got errors: {res.errors}"
    assert res.per_rule_results.get("sentence_structure") is True
    assert res.per_rule_results.get("paragraph_count") is True


def test_system_prompt_dynamic_formatting():
    """Verifies that A2_SYSTEM_PROMPT_V2 dynamically accepts operator-provided rulebook configurations."""
    formatted = A2_SYSTEM_PROMPT_V2.format(
        banned_openers_formatted='   - "custom banned opener"',
        banned_phrases_formatted='"custom_buzzword_1", "custom_buzzword_2"',
        target_words_min=75,
        target_words_max=135,
        target_words_comfort=105,
        max_paragraphs=3,
        max_sentences_per_paragraph=3,
        allowed_products_str="organic cumin, turmeric",
        tone_reference_str="Tone reference: formal",
    )
    assert "custom banned opener" in formatted
    assert "custom_buzzword_1" in formatted
    assert "between 75 and 135 words" in formatted
    assert "roughly 105 words" in formatted
    assert "Exactly 3 paragraphs" in formatted
    assert "2 to 3 complete sentences" in formatted
