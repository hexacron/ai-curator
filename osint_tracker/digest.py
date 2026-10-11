"""Turn untrusted README text into a bounded, sanitised digest for the LLM review.

Sanitising removes the usual hiding places for injected instructions (HTML comments, zero-width, bidi and
Unicode-tag characters, long encoded blobs) and neutralises the delimiter the prompt wraps data in. It does
not make the text trustworthy; the prompt and the output validation do that.
"""
from __future__ import annotations

import re
from typing import List, Optional

DIGEST_CHARS = 6000
LEAD_CHARS = 3000
SECTION_CHARS = 500
OUTLINE_LINES = 40
OUTLINE_CHARS = 1000
SECTIONS_CHARS = 1500

_HIDDEN_CHARS = re.compile('[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\U000e0000-\U000e007f]')
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.S)
_BLOB = re.compile(r'[A-Za-z0-9+/=_-]{120,}')
_DELIMITER = re.compile(r'<\s*/?\s*repository_data\s*>', re.I)
_HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$', re.M)
_USEFUL_SECTION = re.compile(
    r'install|setup|usage|features?|how it works|data sources?|sources|disclaimer|legal|ethic|limitations?|supported|requirements',
    re.I)


def sanitize(text: Optional[str]) -> str:
    """Strip hidden characters, comments, control characters, encoded blobs and delimiter look-alikes."""
    if not text:
        return ''
    text = _HTML_COMMENT.sub('', text)
    text = _HIDDEN_CHARS.sub('', text)
    text = _CONTROL_CHARS.sub('', text)
    text = _DELIMITER.sub('[tag]', text)
    text = _BLOB.sub('[blob]', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def build(readme_text: Optional[str], limit: int = DIGEST_CHARS) -> str:
    """The README's opening, a heading outline, and the start of its install/usage/legal-type sections,
    within `limit` characters. Empty when the repository has no README."""
    text = sanitize(readme_text)
    if not text:
        return ''

    headings = [(m.start(), m.end(), m.group(2)) for m in _HEADING.finditer(text)]
    outline = '\n'.join(f"- {title[:100]}" for _, _, title in headings[:OUTLINE_LINES])

    sections: List[str] = []
    for index, (_, end, title) in enumerate(headings):
        if _USEFUL_SECTION.search(title):
            stop = headings[index + 1][0] if index + 1 < len(headings) else len(text)
            sections.append(f"## {title[:100]}\n{text[end:stop].strip()[:SECTION_CHARS]}")

    # Each part gets its own budget so that a long outline cannot push the key sections off the end.
    tail: List[str] = []
    if outline:
        tail.append(f"[outline]\n{outline[:OUTLINE_CHARS]}")
    if sections:
        tail.append("[key sections]\n" + '\n\n'.join(sections)[:SECTIONS_CHARS])
    rest = '\n\n'.join(tail)
    lead = text[:min(LEAD_CHARS, max(limit - len(rest) - 2, 0))]
    return '\n\n'.join(part for part in (lead, rest) if part)[:limit]
