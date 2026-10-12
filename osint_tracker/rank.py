"""Boards: who is eligible where, ordering, per-owner limits, and rank change since the previous run.

Gates (a repository failing any is absent from every board):
  - tracked, not gone, not archived, scored, not a clone, not `high` sensitivity
  - scope `core` for every board except `adjacent`; `out` is never scored
  - integrity multiplier at or above `unverified_below`
`rising` additionally needs emerging status and enough confidence. `caution`-labelled repositories are included
(the label stays visible on the page); `high` ones never reach any board.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from osint_tracker import state
from osint_tracker.clock import age_days

BOARDS_FILENAME = 'boards.json'
BOARDS_VERSION = 1


def boards_path(data_dir: str) -> Path:
    return Path(data_dir) / BOARDS_FILENAME


def load_previous_ranks(data_dir: str) -> Dict[str, Dict[int, int]]:
    """{board key: {repo id: rank}} from the last run. Derived data, so an unreadable file just means no history."""
    try:
        with open(boards_path(data_dir), 'r', encoding='utf-8') as f:
            boards = json.load(f)['boards']
    except (OSError, ValueError, KeyError):
        return {}
    ranks = {name: {e['id']: e['rank'] for e in entries} for name, entries in boards.items() if name != 'domains'}
    for domain, entries in boards.get('domains', {}).items():
        ranks[f'domains/{domain}'] = {e['id']: e['rank'] for e in entries}
    return ranks


def save(data_dir: str, boards: dict) -> Path:
    path = boards_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(boards, f, indent=1, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)
    return path


def _unverified(record: dict, threshold: float) -> bool:
    return record['score']['integrity']['mult'] < threshold


def _entry(record: dict, profile: str, rank: int, previous: Optional[int], now: datetime) -> dict:
    score, klass = record['score'], record['class']
    return {
        'rank': rank,
        'rank_change': None if previous is None else previous - rank,
        'id': record['id'],
        'full_name': record['full_name'],
        'html_url': record['html_url'],
        'description': record.get('description'),
        'language': record.get('language'),
        'license': record.get('license'),
        'topics': (record.get('topics') or [])[:8],
        'created_at': record['created_at'],
        'age_days': int(age_days(record['created_at'], now)),
        'stars': state.latest(record.get('stars')),
        'forks': state.latest(record.get('forks')),
        'status': score['status'],
        'scope': klass['scope'],
        'domain': klass['domain'],
        'domain_secondary': klass['domain_secondary'],
        'form': klass['form'],
        'ai_role': klass['ai_role'],
        'summary': klass.get('summary'),
        'strengths': klass.get('strengths') or [],
        'risks': klass.get('risks') or [],
        'reviewed': bool(klass.get('reviewed')),
        'readme_language': klass.get('language'),
        'sensitivity': klass['sensitivity'],
        'sensitivity_tags': klass['sensitivity_tags'],
        'score': score['totals'][profile],
        'rank_key': score['rank_keys'][profile],
        'confidence': score['confidence'],
        'components': score['components'],
        'integrity': score['integrity'],
        'star_rate': score['star_rate'],
    }


def _ordered(records: List[dict], profile: str, size: int, owner_cap: int, previous: Dict[int, int], now: datetime) -> List[dict]:
    """Best first, at most `owner_cap` per owner (case-insensitive), at most `size` entries."""
    records = sorted(records, key=lambda r: (-r['score']['rank_keys'][profile], -state.latest(r.get('stars')), r['id']))
    per_owner: Counter = Counter()
    entries = []
    for record in records:
        owner = record['owner'].lower()
        if per_owner[owner] >= owner_cap:
            continue
        per_owner[owner] += 1
        entries.append(_entry(record, profile, len(entries) + 1, previous.get(record['id']), now))
        if len(entries) == size:
            break
    return entries


HISTORY_DAYS = 90


def _history(records: List[dict], boards: dict) -> Dict[str, dict]:
    """Star counts over the last HISTORY_DAYS days for every repository on a board (for the site's sparklines)."""
    on_board = {entry['id'] for name, board in boards.items()
                for entry in (sum(board.values(), []) if name == 'domains' else board)}
    history = {}
    for record in records:
        series = record.get('stars') or []
        if record['id'] not in on_board or not series:
            continue
        kept = series[-HISTORY_DAYS:]
        start = date.fromisoformat(record['snap_start']) + timedelta(days=len(series) - len(kept))
        history[str(record['id'])] = {'start': start.isoformat(), 'stars': kept}
    return history


def build_boards(records: List[dict], now: datetime, config: dict, previous_ranks: Dict[str, Dict[int, int]]) -> dict:
    settings, threshold = config['boards'], config['scoring']['unverified_below']
    size, owner_cap = settings['size'], settings['owner_cap']
    excluded = Counter()
    rejected = []

    eligible = []
    for record in records:
        if not record.get('tier1') or record.get('gone'):
            continue
        if (record.get('class') or {}).get('scope') == 'out':
            excluded['scope_out'] += 1
            rejected.append({'id': record['id'], 'full_name': record['full_name'], 'reason': 'out of scope',
                             'why': record['class']['why']})
            continue
        if record.get('archived') or 'score' not in record:
            continue
        if record.get('clone_of'):
            excluded['clone'] += 1
            rejected.append({'id': record['id'], 'full_name': record['full_name'], 'reason': 'clone',
                             'why': f"copy of repository {record['clone_of']}"})
        elif record['class']['sensitivity'] == 'high':
            excluded['sensitivity_high_hidden'] += 1
        elif _unverified(record, threshold):
            excluded['unverified'] += 1
        else:
            eligible.append(record)

    core = [r for r in eligible if r['class']['scope'] == 'core']
    emerging = [r for r in core if r['score']['status'] == 'emerging']

    rising = [r for r in emerging if r['score']['confidence'] >= settings['min_confidence_rising']]
    fresh = [r for r in emerging if age_days(r['created_at'], now) <= settings['new_days']]

    by_domain = defaultdict(list)
    for record in core:
        if record['class']['domain']:
            by_domain[record['class']['domain']].append(record)

    def ranked(name, items, profile, board_size=size):
        return _ordered(items, profile, board_size, owner_cap, previous_ranks.get(name, {}), now)

    boards = {
        'rising': ranked('rising', rising, 'rising'),
        'new': ranked('new', fresh, 'rising'),
        'established': ranked('established', [r for r in core if r['score']['status'] == 'established'], 'overall'),
        'adjacent': ranked('adjacent', [r for r in eligible if r['class']['scope'] == 'adjacent'], 'overall'),
        'domains': {domain: ranked(f'domains/{domain}', items, 'overall', settings['domain_size'])
                    for domain, items in sorted(by_domain.items())},
    }
    rejected.sort(key=lambda item: item['full_name'].lower())
    return {
        'version': BOARDS_VERSION,
        'generated': now.isoformat(timespec='seconds'),
        'window_days': config['window_days'],
        'scored': sum(1 for r in records if 'score' in r and r.get('tier1') and not r.get('gone')),
        'excluded': dict(excluded),
        'has_previous': bool(previous_ranks),
        'boards': boards,
        'history': _history(records, boards),
        'rejected': rejected,
    }
