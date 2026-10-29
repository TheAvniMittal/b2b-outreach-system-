import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Dict

PROSPECT_HISTORY_THRESHOLD = 0.35
SEGMENT_HISTORY_THRESHOLD = 0.50
OPENING_SENTENCE_THRESHOLD = 0.60
TONE_REFERENCE_THRESHOLD = 0.50


@dataclass
class SamenessResult:
    passed: bool
    max_prospect_similarity: float = 0.0
    max_segment_similarity: float = 0.0
    max_opener_similarity: float = 0.0
    tone_reference_similarity: float = 0.0
    violations: List[str] = field(default_factory=list)


def extract_words(text: str) -> List[str]:
    """Tokenize text into lowercase words without punctuation."""
    return [w.lower() for w in re.findall(r"\b[\w'-]+\b", text) if w]


def extract_word_trigrams(text: str) -> Set[Tuple[str, str, str]]:
    """Extract consecutive word trigrams (3-tuples) from text."""
    words = extract_words(text)
    if len(words) < 3:
        return set()
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def extract_char_trigrams(text: str) -> Set[str]:
    """Extract character trigrams as fallback for short texts."""
    cleaned = re.sub(r"\s+", " ", text.lower().strip())
    if len(cleaned) < 3:
        return set()
    return {cleaned[i : i + 3] for i in range(len(cleaned) - 2)}


def jaccard_similarity(set_a: Set, set_b: Set) -> float:
    """Compute Jaccard similarity index between two sets."""
    if not set_a and not set_b:
        return 0.0
    union_len = len(set_a | set_b)
    if union_len == 0:
        return 0.0
    return len(set_a & set_b) / union_len


def compute_trigram_jaccard(text_a: str, text_b: str) -> float:
    """
    Computes trigram Jaccard similarity between two texts.
    Uses word trigrams, falling back to character trigrams if texts are short.
    """
    tri_a = extract_word_trigrams(text_a)
    tri_b = extract_word_trigrams(text_b)

    if not tri_a or not tri_b:
        # Fall back to character trigrams for short texts
        c_tri_a = extract_char_trigrams(text_a)
        c_tri_b = extract_char_trigrams(text_b)
        return jaccard_similarity(c_tri_a, c_tri_b)

    return jaccard_similarity(tri_a, tri_b)


def extract_opening_sentence(body: str) -> str:
    """
    Extracts the opening sentence of the email body,
    skipping greeting lines like 'Hi <First Name>,'.
    """
    cleaned = body.strip().replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]

    # Skip line 1 greeting if it matches greeting pattern
    start_idx = 0
    if lines and re.match(r"^(?:Hi|Dear)\s+[A-Za-z]+,\s*$", lines[0], re.IGNORECASE):
        start_idx = 1

    remaining_text = " ".join(lines[start_idx:])
    # Split into sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", remaining_text) if s.strip()]
    return sentences[0] if sentences else ""


def hash_sentence(sentence: str) -> str:
    """Produces 8-character hex hash of normalized sentence."""
    normalized = " ".join(extract_words(sentence))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]


def validate_sameness(
    body: str,
    prospect_recent_messages: Optional[List[str]] = None,
    segment_recent_messages: Optional[List[str]] = None,
    recent_opening_sentences: Optional[List[str]] = None,
    opening_hashes: Optional[List[str]] = None,
    phrases_to_avoid: Optional[List[str]] = None,
    banned_openers_ledger: Optional[List[str]] = None,
    tone_sample_reference: Optional[str] = None,
) -> SamenessResult:
    """
    Sameness Gate (§10.1[5]).
    Checks:
    i.   vs this prospect's last 3 messages: trigram Jaccard < 0.35
    ii.  vs last 50 in the SAME segment+step, 14 days: trigram Jaccard < 0.50
    iii. OPENING SENTENCE, stricter: trigram Jaccard < 0.60 against opening_hashes & segment's recent openers
    iv.  No phrase from phrases_to_avoid or campaign-level banned-opener ledger
    v.   vs approved Tone Studio sample draft: trigram Jaccard < 0.50 (A2 must adopt tone only, not copy text)
    """
    violations: List[str] = []
    max_prospect_sim = 0.0
    max_segment_sim = 0.0
    max_opener_sim = 0.0
    max_tone_sim = 0.0

    # Strictly exclude mandatory footer from sameness calculations
    cleaned_body = body.split("\n--\n")[0].split("\n-- \n")[0].strip()

    # 1. Prospect history check (last 3 messages)
    if prospect_recent_messages:
        for idx, prior_msg in enumerate(prospect_recent_messages[:3]):
            clean_prior = prior_msg.split("\n--\n")[0].split("\n-- \n")[0].strip()
            sim = compute_trigram_jaccard(cleaned_body, clean_prior)
            if sim > max_prospect_sim:
                max_prospect_sim = sim
            if sim >= PROSPECT_HISTORY_THRESHOLD:
                violations.append(
                    f"Prospect similarity {sim:.3f} >= {PROSPECT_HISTORY_THRESHOLD} on prior message {idx + 1}"
                )

    # 2. Segment+step history check (last 50 messages)
    if segment_recent_messages:
        for idx, segment_msg in enumerate(segment_recent_messages[:50]):
            clean_seg = segment_msg.split("\n--\n")[0].split("\n-- \n")[0].strip()
            sim = compute_trigram_jaccard(cleaned_body, clean_seg)
            if sim > max_segment_sim:
                max_segment_sim = sim
            if sim >= SEGMENT_HISTORY_THRESHOLD:
                violations.append(
                    f"Segment similarity {sim:.3f} >= {SEGMENT_HISTORY_THRESHOLD} on segment message {idx + 1}"
                )

    # 3. Opening sentence checks
    opener = extract_opening_sentence(cleaned_body)
    if opener:
        opener_hash = hash_sentence(opener)
        if opening_hashes and opener_hash in opening_hashes:
            violations.append(f"Opening sentence hash '{opener_hash}' matches prior prospect opener.")

        if recent_opening_sentences:
            for idx, prior_opener in enumerate(recent_opening_sentences):
                # Calculate character trigram similarity for opening sentence comparison
                char_sim = jaccard_similarity(extract_char_trigrams(opener), extract_char_trigrams(prior_opener))
                word_sim = compute_trigram_jaccard(opener, prior_opener)
                sim = max(char_sim, word_sim)
                if sim > max_opener_sim:
                    max_opener_sim = sim
                if sim >= OPENING_SENTENCE_THRESHOLD:
                    violations.append(
                        f"Opening sentence similarity {sim:.3f} >= {OPENING_SENTENCE_THRESHOLD} against prior opener '{prior_opener}'"
                    )

    # 4. Denylist phrases (phrases_to_avoid and campaign banned openers)
    body_lower = body.lower()
    combined_denylist = (phrases_to_avoid or []) + (banned_openers_ledger or [])
    for phrase in combined_denylist:
        clean_phrase = phrase.strip().lower()
        if not clean_phrase:
            continue
        pattern = r"\b" + re.escape(clean_phrase) + r"\b"
        if re.search(pattern, body_lower):
            violations.append(f"Draft contains avoided phrase: '{phrase}'")

    # 5. Tone reference sample check (A2 must adopt tone only, not copy text)
    if tone_sample_reference:
        clean_tone = tone_sample_reference.split("\n--\n")[0].split("\n-- \n")[0].strip()
        sim_tone = compute_trigram_jaccard(cleaned_body, clean_tone)
        if sim_tone > max_tone_sim:
            max_tone_sim = sim_tone
        if sim_tone >= TONE_REFERENCE_THRESHOLD:
            violations.append(
                f"Tone reference copy violation: similarity {sim_tone:.3f} >= {TONE_REFERENCE_THRESHOLD} to Tone Studio approved sample. A2 must adopt tone only, not copy text."
            )

    passed = len(violations) == 0
    return SamenessResult(
        passed=passed,
        max_prospect_similarity=max_prospect_sim,
        max_segment_similarity=max_segment_sim,
        max_opener_similarity=max_opener_sim,
        tone_reference_similarity=max_tone_sim,
        violations=violations,
    )
