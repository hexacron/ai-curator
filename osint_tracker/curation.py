"""Human overrides, applied after rules and LLM review: <data_dir>/curation.json

    {"pin":   {"owner/repo": {"scope": "core", "domain": "social-media", "sensitivity": "none"}},
     "block": ["owner/other-repo"]}

A pin overwrites the named fields (including lowering sensitivity: a person decided). A blocked repository is
treated as out of scope. Repositories are matched by current or former `owner/name` (case-insensitive) or by id.
The file is edited by hand, so a mistake raises instead of being ignored.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from osint_tracker.classify import AI_ROLES, DOMAIN_NAMES, FORMS, SCOPES, SENSITIVITIES

CURATION_FILENAME = 'curation.json'

_ALLOWED = {'scope': SCOPES, 'domain': DOMAIN_NAMES, 'form': FORMS, 'ai_role': AI_ROLES, 'sensitivity': SENSITIVITIES}


class Curation:
    def __init__(self, pins: Dict[str, dict], blocked: List[str]):
        self.pins = pins
        self.blocked = set(blocked)

    @staticmethod
    def _keys(record: dict) -> set:
        names = [record.get('full_name', '')] + list(record.get('names') or [])
        return {name.lower() for name in names if name} | {str(record['id'])}

    def apply(self, record: dict, klass: dict) -> dict:
        """Return `klass` with this repository's curation applied (unchanged if it has none)."""
        keys = self._keys(record)
        pin = next((self.pins[k] for k in keys if k in self.pins), None)
        blocked = bool(keys & self.blocked)
        if not pin and not blocked:
            return klass

        result = dict(klass)
        result.update(pin or {})
        if blocked:
            result.update(scope='out', domain=None, domain_secondary=None)
        if result.get('scope') == 'out':
            result['domain'] = result['domain_secondary'] = None
        result['source'] = 'curation'
        return result


def load(data_dir: str) -> Curation:
    path = Path(data_dir) / CURATION_FILENAME
    if not path.exists():
        return Curation({}, [])

    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as e:
        raise ValueError(f"Cannot read {path}: {e}") from e
    if not isinstance(raw, dict) or set(raw) - {'pin', 'block'}:
        raise ValueError(f"{path} may only contain 'pin' and 'block'")

    pins = {}
    for name, fields in (raw.get('pin') or {}).items():
        if not isinstance(fields, dict) or not fields:
            raise ValueError(f"{path}: pin '{name}' must be an object with at least one field")
        for field, value in fields.items():
            if field not in _ALLOWED or value not in _ALLOWED[field]:
                raise ValueError(f"{path}: pin '{name}' has invalid {field}={value!r}")
        pins[str(name).lower()] = fields

    block = raw.get('block') or []
    if not isinstance(block, list) or not all(isinstance(b, (str, int)) for b in block):
        raise ValueError(f"{path}: 'block' must be a list of repository names or ids")
    return Curation(pins, [str(b).lower() for b in block])
