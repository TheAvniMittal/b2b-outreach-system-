import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

# Layer 1 & 2 definitions

CURRENCY_SYMBOLS_AND_CODES = [
    r"\$",
    r"₹",
    r"€",
    r"£",
    r"¥",
    r"\bAED\b",
    r"\bEUR\b",
    r"\bUSD\b",
    r"\bINR\b",
    r"\bGBP\b",
    r"\bRs\.?\b",
]

TRADE_UNITS = [
    r"\bMT\b",
    r"\bmetric\s+tonnes?\b",
    r"\bmetric\s+tons?\b",
    r"\btonnes?\b",
    r"\btons?\b",
    r"\bkgs?\b",
    r"\bkilograms?\b",
    r"\bcartons?\b",
    r"\bcontainers?\b",
    r"\bpallets?\b",
    r"\bunits?\b",
    r"\bpcs\b",
    r"\bpieces?\b",
    r"\bFCL\b",
    r"\bLCL\b",
]

MINIMUM_KEYWORDS = [
    r"\bMOQ\b",
    r"\bminimums?\b",
    r"\bmin\s+orders?\b",
    r"\bminimum\s+orders?\b",
    r"\bminimum\s+quantity\b",
    r"\bminimum\s+purchase\b",
]

SHELF_LIFE_KEYWORDS = [
    r"\bshelf[- ]life\b",
    r"\bbest[- ]before\b",
]

PAYMENT_KEYWORDS = [
    r"\badvance\b",
    r"\bnet\s+(?:15|30|45|60|90)\b",
    r"\bcredit\s+period\b",
    r"\bLC\b",
    r"\bTT\b",
    r"\bletter\s+of\s+credit\b",
    r"\btelegraphic\s+transfer\b",
]

LEAD_TIME_KEYWORDS = [
    r"\blead[- ]time\b",
    r"\bships?\s+in\b",
    r"\bdispatch(?:ed|es)?\s+in\b",
    r"\bdispatch\s+window\b",
    r"\bdelivery\s+time\b",
    r"\bdelivery\s+in\b",
]

INCENTIVE_KEYWORDS = [
    r"\bmargins?\b",
    r"\bdiscounts?\b",
    r"\brebates?\b",
    r"\bslabs?\b",
]

# All commercial keywords & units that trigger numeric proximity
ALL_COMMERCIAL_TERMS = (
    CURRENCY_SYMBOLS_AND_CODES
    + TRADE_UNITS
    + MINIMUM_KEYWORDS
    + SHELF_LIFE_KEYWORDS
    + PAYMENT_KEYWORDS
    + LEAD_TIME_KEYWORDS
    + INCENTIVE_KEYWORDS
)

NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "dozen", "lakh", "lakhs", "crore", "crores",
}

# Indian currency formats: "Rs.380/-", "INR 380", "380/-", "Rs 380", "Rs. 380"
INDIAN_CURRENCY_PATTERNS = [
    re.compile(r"\bRs\.?\s*\d+(?:\s*\/-)?", re.IGNORECASE),
    re.compile(r"\bINR\s*\d+", re.IGNORECASE),
    re.compile(r"\b\d+\s*\/-", re.IGNORECASE),
]

# Layer 3: Unit-Free Commitment Denylist (Unconditional)
UNCONDITIONAL_DENYLIST = [
    r"\bcompetitive\s+pricing\b",
    r"\bbest\s+price\b",
    r"\bspecial\s+rates?\b",
    r"\bflexible\s+on\s+minimums\b",
    r"\bminimums\s+are\s+flexible\b",
    r"\bflexible\s+minimums?\b",
    r"\bdiscounts?\s+available\b",
    r"\battractive\s+terms\b",
    r"\bnegotiable\b",
    r"\bas\s+low\s+as\b",
    r"\bexclusive\s+distributorship\b",
    r"\bsole\s+rights\b",
]

# Layer 3: Conditional ("we can offer" + commercial noun fires; + catalogue/samples passes)
CONDITIONAL_OFFER_PREFIXES = r"(?:we\s+can\s+offer|we\s+are\s+able\s+to\s+offer|happy\s+to\s+offer)"
COMMERCIAL_NOUNS = r"(?:pricing|rates?|terms|discounts?|margins?|minimums?|credit)"
CONDITIONAL_COMMERCIAL_PATTERN = re.compile(
    rf"\b{CONDITIONAL_OFFER_PREFIXES}\s+(?:\w+\s+){{0,3}}{COMMERCIAL_NOUNS}\b",
    re.IGNORECASE,
)


@dataclass
class CommercialGateResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


def clean_token(token: str) -> str:
    """Strip leading/trailing punctuation except currency symbols."""
    return re.sub(r"^[^\w$₹€£¥]+|[^\w%]+$", "", token.lower())


def is_numeric_token(token: str) -> bool:
    """Check if token is a digit-based numeric or a spelled number word."""
    cleaned = clean_token(token)
    if not cleaned:
        return False
    # Pure digits or float or percentage: e.g. 40, 4.20, 30%
    if re.match(r"^\d+(?:\.\d+)?%?$", cleaned):
        return True
    if cleaned in NUMBER_WORDS:
        return True
    return False


def _check_layer_1_and_2(text: str) -> List[str]:
    """
    Checks Layer 1 (numeric proximity to commercial keywords/units)
    and Layer 2 (number words treated as numerics, Indian currency forms).
    """
    violations = []

    # Check Indian currency forms directly
    for pattern in INDIAN_CURRENCY_PATTERNS:
        match = pattern.search(text)
        if match:
            violations.append(f"Indian currency format detected: '{match.group()}'")

    # Check percentage proximity to advance/discount/margin e.g. "30% advance", "8% margin"
    percent_match = re.search(r"\b\d+%\s*(?:advance|margin|discount|rebate)\b", text, re.IGNORECASE)
    if percent_match:
        violations.append(f"Percentage commercial term detected: '{percent_match.group()}'")

    # Tokenize preserving character spans
    tokens = list(re.finditer(r"\S+", text))
    token_count = len(tokens)

    # Compile all commercial regexes
    compiled_terms = [(term, re.compile(term, re.IGNORECASE)) for term in ALL_COMMERCIAL_TERMS]

    for term_str, term_re in compiled_terms:
        for match in term_re.finditer(text):
            start_char, end_char = match.span()
            matched_phrase = match.group()

            # Find matching token index
            match_token_idx = -1
            for idx, tok_match in enumerate(tokens):
                t_start, t_end = tok_match.span()
                if (t_start <= start_char < t_end) or (start_char <= t_start < end_char):
                    match_token_idx = idx
                    break

            if match_token_idx == -1:
                continue

            # Scan +/- 3 tokens around match_token_idx
            left_bound = max(0, match_token_idx - 3)
            right_bound = min(token_count, match_token_idx + 4)

            for scan_idx in range(left_bound, right_bound):
                tok_str = tokens[scan_idx].group()
                if scan_idx == match_token_idx:
                    # Attached digits, e.g. "$4.20", "₹380", "40MT"
                    if re.search(r"\d", tok_str):
                        violations.append(
                            f"Commercial term '{matched_phrase}' adjacent to numeric '{tok_str}'"
                        )
                    continue

                if is_numeric_token(tok_str):
                    violations.append(
                        f"Commercial term '{matched_phrase}' adjacent to numeric '{tok_str}'"
                    )

    return violations


def _check_layer_3(text: str) -> List[str]:
    """
    Checks Layer 3: Unit-Free Commitment Denylist (unconditional and conditional).
    """
    violations = []

    # Unconditional denylist
    for phrase in UNCONDITIONAL_DENYLIST:
        match = re.search(phrase, text, re.IGNORECASE)
        if match:
            violations.append(f"Unit-free commercial commitment detected: '{match.group()}'")

    # Conditional offer before commercial noun
    cond_match = CONDITIONAL_COMMERCIAL_PATTERN.search(text)
    if cond_match:
        violations.append(f"Commercial offer commitment detected: '{cond_match.group()}'")

    return violations


def validate_commercial_terms(body: str, subject: Optional[str] = None) -> CommercialGateResult:
    """
    Commercial-term gate (§10.8).
    Overrides grounding — even if grounded in a brief, commercial terms are rejected.
    Categorically validates that no commercial commitments (Layer 1, Layer 2, Layer 3)
    appear in email body or subject.
    """
    combined_violations = []

    # Check subject if present
    if subject:
        subj_l12 = _check_layer_1_and_2(subject)
        subj_l3 = _check_layer_3(subject)
        for v in subj_l12 + subj_l3:
            combined_violations.append(f"[Subject] {v}")

    # Check body
    body_l12 = _check_layer_1_and_2(body)
    body_l3 = _check_layer_3(body)
    combined_violations.extend(body_l12 + body_l3)

    # Deduplicate violations while preserving order
    unique_violations = list(dict.fromkeys(combined_violations))

    return CommercialGateResult(
        passed=len(unique_violations) == 0,
        violations=unique_violations,
    )
