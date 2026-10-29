import re

QUOTE_SPLIT_PATTERNS = [
    re.compile(r"^[-_\s]*original message[-_\s]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[-_\s]*forwarded message[-_\s]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*on\s.+wrote:\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*from:\s.+subject:\s.+$", re.IGNORECASE | re.MULTILINE | re.DOTALL),
]


def strip_email_quotes(body: str, max_chars: int = 2000) -> str:
    """
    Strips quoted reply chains, forwarding blocks, and truncates to max_chars
    per VISION.md §7.5 and §8.7.
    """
    if not body:
        return ""

    # Normalize line endings
    text = body.replace("\r\n", "\n").replace("\r", "\n")

    # 1. Truncate at common "On ... wrote:" or "Original Message" divider
    for pattern in QUOTE_SPLIT_PATTERNS:
        match = pattern.search(text)
        if match:
            text = text[:match.start()]
            break

    # 2. Filter out individual quote lines starting with '>'
    clean_lines = []
    for line in text.split("\n"):
        stripped_line = line.strip()
        if stripped_line.startswith(">"):
            continue
        clean_lines.append(line)

    cleaned = "\n".join(clean_lines).strip()

    # 3. Truncate to maximum characters (2,000 chars per VISION.md §7.5)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()

    return cleaned


def fence_untrusted_data(text: str) -> str:
    """
    Wraps text strictly in <untrusted_data> tags per VISION.md §7.5, §8.8.
    """
    return f"<untrusted_data>\n{text}\n</untrusted_data>"
