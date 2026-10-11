"""Deterministic README facts: size, a normalised content hash, and a checklist found with plain text rules.

README text is untrusted and never persisted; only these derived facts are."""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional

SCAN_LIMIT = 60_000  # characters; bounds regex work on very large READMEs

_BADGE_URL = re.compile(r'shields\.io|badge|badgen|travis-ci|codecov|coveralls|/actions/workflows/', re.I)
_IMAGE = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)|<img\b[^>]*?\bsrc=["\']([^"\']+)', re.I)
_FENCE = re.compile(r'^\s*(```|~~~)', re.M)

_INSTALL_HEADING = re.compile(
    r'^#{1,6}\s*.*\b(install(ation)?|setup|getting started|quick ?start|requirements|установк\w*|安装)', re.I | re.M)
_INSTALL_COMMAND = re.compile(
    r'\b(pip3?|pipx|npm|yarn|pnpm|cargo|brew|apt(-get)?|gem|composer) (install|add)\b|\bgo (install|get)\b'
    r'|docker (run|pull|compose)|git clone|\bsetup\.sh\b|\binstall\.sh\b', re.I)
_USAGE_HEADING = re.compile(
    r'^#{1,6}\s*.*\b(usage|examples?|how to use|demo|commands?|cli|using|использован\w*|用法|使用)', re.I | re.M)
_LEGAL = re.compile(
    r'disclaimer|legal (notice|use|disclaimer)|\blawful|authori[sz]ed (use|testing|engagements?|purposes?)|\bethic(al|s)\b'
    r'|educational purposes?|responsible(ly)? use|at your own risk|terms of service|\bconsent\b|законн\w+|免责声明|合法', re.I)
_API_KEYS = re.compile(r'api[ _-]?keys?|access tokens?|\.env\b|environment variables?|secret keys?', re.I)

CHECK_NAMES = ('install', 'usage', 'screenshot', 'legal_notice', 'api_keys')


def _has_screenshot(text: str) -> bool:
    return any(not _BADGE_URL.search(match[0] or match[1]) for match in _IMAGE.findall(text))


def analyze(text: Optional[str], byte_size: int = 0) -> Dict:
    """Facts about one README: its size, a whitespace/case-insensitive hash (identical hashes mean copies),
    and which of the standard sections it has."""
    if not text:
        return {'bytes': byte_size or 0, 'sha256': None, 'checks': {name: False for name in CHECK_NAMES}}

    head = text[:SCAN_LIMIT]
    normalised = ' '.join(head.split()).lower()
    return {
        'bytes': byte_size or len(text.encode('utf-8')),
        'sha256': hashlib.sha256(normalised.encode('utf-8')).hexdigest(),
        'checks': {
            'install': bool(_INSTALL_HEADING.search(head) or _INSTALL_COMMAND.search(head)),
            'usage': bool(_USAGE_HEADING.search(head) or len(_FENCE.findall(head)) >= 4),  # 2+ fenced blocks
            'screenshot': _has_screenshot(head),
            'legal_notice': bool(_LEGAL.search(head)),
            'api_keys': bool(_API_KEYS.search(head)),
        },
    }
