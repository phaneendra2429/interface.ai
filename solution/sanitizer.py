"""
Text and Markdown sanitization utilities.
Cleans raw crawl artifacts, country code phone dropdowns, and footnote concatenations
while strictly preserving numeric precision ($24,999.99, 2027, $5.99).
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse
from typing import Optional

# Pattern matching international dialing code dropdown items
COUNTRY_DROPDOWN_RE = re.compile(
    r"^[-*]\s+(?:[\U0001F1E6-\U0001F1FF]{2}|[\u2000-\u206F]|\b[A-Za-z\s]+)\s*(?:\([^)]+\))?\s*\+\d{1,4}\s*$",
    re.M,
)

# Pattern matching trailing boilerplate blocks
BOILERPLATE_BLOCKS_RE = re.compile(
    r"(?:Twitter Widget Iframe|Messenger Launcher|Third Party Plugins|Messenger\b.*)",
    re.I,
)

# Non-destructive footnote regex: MUST require explicit %, percent, APR, or APY
# before matching a trailing single footnote digit (1-9).
# Prevents truncating $24,999.99 -> $24,999.9 or 2027 -> 202.
FOOTNOTE_NUM_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|percent)(?:\s*(?:APR|APY))?|\d+(?:\.\d+)?\s*(?:APR|APY))([1-9])(?=\s*(?:\||\n|$|\*|\s))",
    re.I,
)

# Effective date and expiry date patterns
EFFECTIVE_DATE_RE = re.compile(
    r"(?:rates\s+(?:are\s+)?effective\s+(?:as\s+of\s+)?|effective\s+(?:date\s*[:\-]?\s*)?|as\s+of\s+)(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)

EXPIRY_DATE_RE = re.compile(
    r"(?:offer\s+ended|expires?|expired|good\s+through|valid\s+until)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)


def clean_markdown_text(text: str) -> str:
    """Sanitize raw markdown text for entity extraction while preserving data fidelity."""
    if not text:
        return ""

    # Remove country code dropdown lines
    text = COUNTRY_DROPDOWN_RE.sub("", text)

    # Remove boilerplate lines
    text = BOILERPLATE_BLOCKS_RE.sub("", text)

    # Normalize footnote markers attached strictly to rates/percentages in tables
    def fix_footnote(match):
        return match.group(1).strip()

    text = FOOTNOTE_NUM_RE.sub(fix_footnote, text)

    # Replace en-dashes and em-dashes with standard hyphens for range consistency
    text = text.replace("–", "-").replace("—", "-")

    return text


def clean_url_path(url: str) -> str:
    """Extract and normalize the path from a URL, ignoring query params and hash."""
    if not url:
        return ""
    p = urlparse(url if "//" in url else "https://" + url)
    netloc = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/").lower()
    return f"{netloc}{path}"


def is_modal_url(url: str) -> bool:
    """Detect if URL is a modal popup or campaign tracking redirect."""
    url_l = url.lower()
    return "containertype=modal" in url_l or "campaignid=" in url_l


def extract_effective_date(text: str) -> Optional[str]:
    """Extract effective date string normalized to YYYY-MM-DD or MM/DD/YYYY."""
    m = EFFECTIVE_DATE_RE.search(text)
    if m:
        raw = m.group(1)
        if "/" in raw:
            parts = raw.split("/")
            if len(parts) == 3:
                return f"{int(parts[2]):04d}-{int(parts[0]):02d}-{int(parts[1]):02d}"
        return raw
    return None


def extract_expiry_date(text: str) -> Optional[str]:
    """Extract promotional expiry date normalized to YYYY-MM-DD."""
    m = EXPIRY_DATE_RE.search(text)
    if m:
        raw = m.group(1)
        if "/" in raw:
            parts = raw.split("/")
            if len(parts) == 3:
                return f"{int(parts[2]):04d}-{int(parts[0]):02d}-{int(parts[1]):02d}"
        return raw
    return None


def extract_footnotes(text: str) -> dict[str, str]:
    """Extract numbered footnote paragraphs, e.g. '1 Rates are effective as of...'."""
    footnotes = {}
    lines = text.splitlines()
    fn_re = re.compile(r"^[\*\s]*([1-9])\s+(.+)$")
    for line in lines:
        line_s = line.strip()
        m = fn_re.match(line_s)
        if m:
            fn_num = m.group(1)
            fn_content = m.group(2).strip()
            footnotes[fn_num] = fn_content
    return footnotes
