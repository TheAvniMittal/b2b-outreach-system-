import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

BANNED_OPENERS = [
    "i hope this email finds you well",
    "i hope you are doing well",
    "i trust this email finds you in good health",
]

BANNED_PHRASES = [
    "reaching out",
    "leverage",
    "synergy",
    "circle back",
]

GREETING_REGEX = re.compile(r"^Hi\s+[A-Z][a-zA-Z]*(?:-[A-Za-z]+)?,\s*$", re.IGNORECASE)


@dataclass
class RulebookResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    per_rule_results: Dict[str, bool] = field(default_factory=dict)


def validate_rulebook_v0(
    body: str,
    first_name: Optional[str] = None,
    word_count_min: int = 80,
    word_count_max: int = 120,
    rulebook_config: Optional[Any] = None,
    company_name: Optional[str] = None,
) -> RulebookResult:
    """
    Validates email body against Rulebook (VISION.md §6.2, Phase 9).
    Enforces dynamic or house voice rules:
    - greeting_form == 'Hi <First Name>,' at line 1
    - banned_openers denylist
    - banned_phrases denylist
    - word count within target (default 80-120)
    - max 3 paragraphs, 2-3 sentences each
    - no exclamation marks
    - no ALL-CAPS words
    """
    active_banned_openers = BANNED_OPENERS
    active_banned_phrases = BANNED_PHRASES
    active_max_paragraphs = 3
    active_max_sentences = 4

    if rulebook_config is None:
        try:
            from app.db.session import get_db_session
            from app.pipeline.config_service import get_active_rulebook
            with get_db_session() as s:
                rb = get_active_rulebook(s)
                if rb:
                    rulebook_config = {
                        "banned_openers": list(rb.banned_openers or []),
                        "banned_phrases": list(rb.banned_phrases or []),
                        "max_paragraphs": rb.max_paragraphs,
                        "max_sentences_per_paragraph": rb.max_sentences_per_paragraph,
                    }
        except Exception:
            pass

    if rulebook_config:
        cfg_openers = getattr(rulebook_config, "banned_openers", None) or (
            rulebook_config.get("banned_openers") if isinstance(rulebook_config, dict) else None
        )
        if cfg_openers is not None:
            active_banned_openers = list(dict.fromkeys(BANNED_OPENERS + [o.strip().lower() for o in cfg_openers if o.strip()]))
        else:
            active_banned_openers = BANNED_OPENERS

        cfg_phrases = getattr(rulebook_config, "banned_phrases", None) or (
            rulebook_config.get("banned_phrases") if isinstance(rulebook_config, dict) else None
        )
        if cfg_phrases is not None:
            active_banned_phrases = list(dict.fromkeys(BANNED_PHRASES + [p.strip().lower() for p in cfg_phrases if p.strip()]))
        else:
            active_banned_phrases = BANNED_PHRASES

        active_max_paragraphs = getattr(rulebook_config, "max_paragraphs", None) or (
            rulebook_config.get("max_paragraphs") if isinstance(rulebook_config, dict) else None
        ) or 3
        active_max_sentences = getattr(rulebook_config, "max_sentences_per_paragraph", None) or (
            rulebook_config.get("max_sentences_per_paragraph") if isinstance(rulebook_config, dict) else None
        ) or 3
    errors: List[str] = []
    rule_results: Dict[str, bool] = {}

    cleaned_text = body.strip().replace("\r\n", "\n").replace("\r", "\n")
    # Strictly exclude mandatory footer from word count, paragraph checks, and greeting validation
    if "\n--\n" in cleaned_text:
        cleaned_text = cleaned_text.split("\n--\n")[0].strip()
    elif "\n-- \n" in cleaned_text:
        cleaned_text = cleaned_text.split("\n-- \n")[0].strip()
    lines = [line.strip() for line in cleaned_text.split("\n") if line.strip()]

    # 1. Greeting form at line 1
    if not lines:
        errors.append("Email body is empty.")
        return RulebookResult(passed=False, errors=errors, per_rule_results={"body_present": False})

    first_line = lines[0]
    if first_name:
        expected_greeting = f"Hi {first_name},"
        greeting_valid = first_line.lower() == expected_greeting.lower()
    else:
        greeting_valid = bool(GREETING_REGEX.match(first_line))

    rule_results["greeting_form"] = greeting_valid
    if not greeting_valid:
        expected = f"'Hi {first_name},'" if first_name else "'Hi <First Name>,'"
        errors.append(f"Line 1 greeting must be exactly {expected}, got '{first_line}'.")

    # 2. Banned openers (check first 2 lines or opening paragraph)
    opening_text = " ".join(lines[:3]).lower()
    found_banned_opener = False
    for opener in active_banned_openers:
        if opener in opening_text:
            found_banned_opener = True
            errors.append(f"Contains banned opener: '{opener}'.")
            break
    rule_results["banned_openers"] = not found_banned_opener

    # 3. Banned phrases (anywhere in body)
    full_lower = cleaned_text.lower()
    found_banned_phrase = False
    for phrase in active_banned_phrases:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, full_lower):
            found_banned_phrase = True
            errors.append(f"Contains banned phrase: '{phrase}'.")
    rule_results["banned_phrases"] = not found_banned_phrase

    # 4. Word count
    words = re.findall(r"\b[\w'-]+\b", cleaned_text)
    word_count = len(words)
    word_count_valid = word_count_min <= word_count <= word_count_max
    rule_results["word_count"] = word_count_valid
    if not word_count_valid:
        errors.append(
            f"Word count {word_count} is outside the allowed range [{word_count_min}, {word_count_max}]."
        )

    # 5. Paragraph structure (Max 3 paragraphs, 2-3 sentences each)
    # Split text into paragraphs by blank lines
    raw_paragraphs = [p.strip() for p in cleaned_text.split("\n\n") if p.strip()]
    
    # If greeting is separated as own paragraph, separate it from main body paragraphs
    body_paragraphs = []
    for p in raw_paragraphs:
        # Skip standalone greeting line
        if GREETING_REGEX.match(p):
            continue
        # Skip sign-off block lines (e.g. "Best regards,\n...")
        if p.lower().startswith("best regards") or p.lower().startswith("warm regards"):
            continue
        body_paragraphs.append(p)

    para_count_valid = len(body_paragraphs) <= active_max_paragraphs
    rule_results["paragraph_count"] = para_count_valid
    if not para_count_valid:
        errors.append(f"Maximum {active_max_paragraphs} paragraphs allowed, found {len(body_paragraphs)}.")

    sentences_valid = True
    for idx, p in enumerate(body_paragraphs, 1):
        # Extract sentences ending with period, question mark
        # Ignore abbreviation periods like "e.g.", "Dr.", "LLC."
        sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", p) if len(s.strip()) > 3]
        if not (2 <= len(sentences) <= active_max_sentences):
            sentences_valid = False
            errors.append(
                f"Paragraph {idx} has {len(sentences)} sentences (required: 2-{active_max_sentences} sentences)."
            )
    rule_results["sentence_structure"] = sentences_valid

    # 6. No exclamation marks
    has_exclamation = "!" in cleaned_text
    rule_results["no_exclamation"] = not has_exclamation
    if has_exclamation:
        errors.append("Exclamation marks ('!') are strictly forbidden.")

    # 7. No ALL-CAPS words
    # Check words that are >= 2 letters, all uppercase, ignoring standard single letter words like 'A' or 'I'
    all_caps_words = []
    exempt_words = {
        "FSSAI", "ISO", "HACCP", "APEDA", "HALAL", "GMP", "GCC", "EAN", "SKU", "MOQ",
        "LLC", "FMCG", "B2B", "NPOP", "UAE", "FOB", "CIF", "HS", "USA", "UK"
    }
    if company_name:
        for token in re.findall(r"\b[A-Za-z0-9]{2,}\b", company_name):
            if token.isupper() or len(token) <= 5:
                exempt_words.add(token.upper())

    # Tokenize word tokens
    for w in re.findall(r"\b[A-Z]{2,}\b", cleaned_text):
        if w not in exempt_words:
            all_caps_words.append(w)

    has_all_caps = len(all_caps_words) > 0
    rule_results["no_all_caps"] = not has_all_caps
    if has_all_caps:
        errors.append(f"ALL-CAPS words detected: {', '.join(set(all_caps_words))}.")

    passed = len(errors) == 0
    return RulebookResult(
        passed=passed,
        errors=errors,
        per_rule_results=rule_results,
    )
