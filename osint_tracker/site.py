"""Static OSINT radar page: `docs/osint/index.html` (one self-contained file) and `docs/osint/data.json`,
generated from <data_dir>/boards.json.

    python -m osint_tracker.site [--data-dir data/osint] [--out docs/osint]

The page inlines its CSS, JS and data and carries a Content-Security-Policy that forbids any other origin.
Repository text is untrusted: it travels as JSON (escaped so it cannot close the script tag) and the page only
ever sets it through textContent.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from osint_tracker.rank import boards_path

TEMPLATE_DIR = Path(__file__).parent / 'site_template'
OUTPUT_HTML = 'index.html'
OUTPUT_DATA = 'data.json'

logger = logging.getLogger(__name__)


def public_payload(boards: dict) -> dict:
    """The part of boards.json the public page may show: no `rejected` list, and never an entry of `high`
    sensitivity even if one slipped into the boards (belt and braces: rank.py already excludes them)."""
    def visible(entries: List[dict]) -> List[dict]:
        return [entry for entry in entries if entry.get('sensitivity') != 'high']

    raw = boards['boards']
    cleaned = {name: visible(entries) for name, entries in raw.items() if name != 'domains'}
    cleaned['domains'] = {domain: visible(entries) for domain, entries in (raw.get('domains') or {}).items()}

    shown = {entry['id'] for name, entries in cleaned.items()
             for entry in (sum(entries.values(), []) if name == 'domains' else entries)}
    return {
        'version': boards.get('version'),
        'generated': boards.get('generated'),
        'window_days': boards.get('window_days'),
        'stats': boards.get('stats') or {},
        'has_previous': bool(boards.get('has_previous')),
        'boards': cleaned,
        'history': {key: series for key, series in (boards.get('history') or {}).items() if int(key) in shown},
    }


def embed_json(payload: dict) -> str:
    """JSON safe to place inside <script type="application/json">: nothing in it can end the script element or
    start a comment, whatever the repository text contains."""
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    for char, escape in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        text = text.replace(char, escape)
    return text


def render(payload: dict) -> str:
    """The complete page. The data is substituted last so that nothing in it can be mistaken for a placeholder."""
    page = (TEMPLATE_DIR / 'index.html').read_text(encoding='utf-8')
    for placeholder, filename in (('/*__STYLE__*/', 'style.css'), ('/*__LOGIC__*/', 'logic.js'), ('/*__UI__*/', 'ui.js')):
        page = page.replace(placeholder, (TEMPLATE_DIR / filename).read_text(encoding='utf-8'))
    return page.replace('/*__DATA__*/', embed_json(payload))


def generate(data_dir: str, out_dir: str) -> Path:
    """Write the page and its data.json. Raises if boards.json is missing or unreadable, so the deployed
    page is never silently replaced by an empty one."""
    source = boards_path(data_dir)
    try:
        boards = json.loads(source.read_text(encoding='utf-8'))
        payload = public_payload(boards)
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"Cannot build the OSINT page from {source}: {e}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / OUTPUT_DATA).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    page = out / OUTPUT_HTML
    page.write_text(render(payload), encoding='utf-8')
    return page


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m osint_tracker.site', description='Generate the OSINT radar page.')
    parser.add_argument('--data-dir', default='data/osint', help='directory holding boards.json')
    parser.add_argument('--out', default='docs/osint', help='output directory')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    page = generate(args.data_dir, args.out)
    logger.info(f"Wrote {page} ({page.stat().st_size // 1024} KB)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
