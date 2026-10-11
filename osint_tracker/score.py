"""Deterministic scoring.

    total = 100 * relevance * integrity * (weighted mean of the available components)
    rank_key = total - confidence_penalty * (1 - confidence)

Components are in [0, 1]. `utility` comes from the LLM review and counts only when the model backed it with a
verified quote; where it is missing the weights of the remaining components are renormalised. Every input is
stored on the record, so changing weights re-scores without refetching anything.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from osint_tracker import state
from osint_tracker.clock import age_days, parse_ts

RECENT_DAYS = 7              # momentum switches from lifetime average to recent growth over this span
LIFETIME_SMOOTHING_DAYS = 14  # stars / (age + 14): a day-old repository with 3 stars is not "3 stars/day"
NO_ISSUES_CLOSURE = 0.3       # closure score when there is too little issue history to judge

# Multipliers for integrity signals. They are signals to review, never accusations.
PENALTIES = {
    'batch_publisher': 0.85,       # owner has many candidates in the window
    'new_owner_account': 0.9,      # owner account created around the time of the repository
    'stars_without_forks': 0.8,    # 100+ stars but almost nobody forked it
    'watchers_outlier': 0.85,      # watchers far out of proportion to stars
    'unclosed_issue_pile': 0.8,    # many issues, none ever closed, nobody forked
    'clone': 0.5,                  # copy of an earlier repository
}
MIN_INTEGRITY = 0.3
SCORED_SCOPES = ('core', 'adjacent')


def is_scorable(record: dict) -> bool:
    return bool(record.get('tier1') and not record.get('gone') and record.get('enrich')
                and (record.get('class') or {}).get('scope') in SCORED_SCOPES)


def is_emerging(record: dict, now: datetime, window_days: int) -> bool:
    """Created within the window and not carrying older history (mirrors and re-uploads are not emerging).
    A repository whose first commit could not be looked up yet is given the benefit of the doubt."""
    cutoff = now - timedelta(days=window_days)
    if parse_ts(record['created_at']) < cutoff:
        return False
    first_commit = record.get('earliest_commit')
    return first_commit is None or parse_ts(first_commit) >= cutoff


def percentile_ranks(values: List[float]) -> List[float]:
    """Mid-rank percentile of each value within the list, in (0, 1). Ties share a rank."""
    ordered = sorted(values)
    total = len(values)
    result = []
    for value in values:
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        result.append((below + equal / 2) / total)
    return result


def growth_rate(series: List[Optional[int]], age: float) -> float:
    """Units gained per day. Blends the lifetime average with growth over the last RECENT_DAYS,
    moving to pure recent growth as soon as that many days of snapshots exist."""
    points = [(i, v) for i, v in enumerate(series or []) if v is not None]
    if not points:
        return 0.0
    last_index, last_value = points[-1]
    lifetime = last_value / (age + LIFETIME_SMOOTHING_DAYS)

    reference = next((p for p in reversed(points) if p[0] <= last_index - RECENT_DAYS), points[0])
    span = last_index - reference[0]
    if span <= 0:
        return lifetime
    recent = max(0.0, (last_value - reference[1]) / span)
    weight = min(1.0, span / RECENT_DAYS)
    return weight * recent + (1 - weight) * lifetime


def momentum_raw(record: dict, now: datetime) -> float:
    age = age_days(record['created_at'], now)
    return math.log1p(growth_rate(record.get('stars'), age)) + 0.5 * math.log1p(growth_rate(record.get('forks'), age))


def _release_score(latest_release: Optional[str], releases: int, now: datetime) -> float:
    if not releases:
        return 0.0
    if not latest_release:
        return 0.2
    days = (now - parse_ts(latest_release)).days
    return 1.0 if days <= 30 else 0.5 if days <= 90 else 0.2


def activity(facts: dict, now: datetime) -> float:
    commits = min(1.0, math.log1p(facts.get('commits_30d') or 0) / math.log1p(60))
    last_commit = facts.get('last_commit')
    recency = math.exp(-max((now - parse_ts(last_commit)).days, 0) / 14) if last_commit else 0.0
    return 0.6 * commits + 0.25 * recency + 0.15 * _release_score(facts.get('latest_release'), facts.get('releases') or 0, now)


def maturity(record: dict, facts: dict) -> float:
    readme, files = facts['readme'], facts['files']
    size = readme['bytes']
    checks = readme['checks']
    has_license = record.get('license') not in (None, 'NOASSERTION')
    parts = [
        (0.20, has_license),
        (0.10, 0.0 if size < 500 else 0.5 if size < 2000 else 1.0),
        (0.15, checks['install']),
        (0.15, checks['usage']),
        (0.05, checks['legal_notice']),
        (0.10, files['tests']),
        (0.10, files['ci']),
        (0.10, files['package'] or files['docker']),
        (0.05, checks['screenshot']),
    ]
    return sum(weight * float(value) for weight, value in parts)


def community(facts: dict, stars: int, forks: int) -> float:
    issues, closed = facts.get('issues') or 0, facts.get('issues_closed') or 0
    closure = closed / issues if issues >= 3 else NO_ISSUES_CLOSURE
    pull_requests = min(1.0, math.log1p(facts.get('prs') or 0) / math.log1p(30))
    authors = min(1.0, max((facts.get('authors_30d') or 0) - 1, 0) / 4)
    ratio = forks / stars if stars else 0.0
    band = 0.0 if ratio < 0.01 else 1.0 if ratio <= 0.5 else 0.7
    if 0.01 <= ratio < 0.03:
        band = (ratio - 0.01) / 0.02
    return 0.30 * closure + 0.25 * pull_requests + 0.25 * authors + 0.20 * band


def integrity(record: dict, facts: dict, stars: int, forks: int) -> Tuple[float, List[str]]:
    flags = []
    if record.get('batch_publisher'):
        flags.append('batch_publisher')
    owner_created = facts.get('owner_created')
    if owner_created and parse_ts(owner_created) >= parse_ts(record['created_at']) - timedelta(days=30):
        flags.append('new_owner_account')
    if stars >= 100 and forks / stars < 0.02:
        flags.append('stars_without_forks')
    watchers = facts.get('watchers') or 0
    if stars >= 50 and watchers >= 10 and watchers / stars > 0.08:
        flags.append('watchers_outlier')
    issues, closed = facts.get('issues') or 0, facts.get('issues_closed') or 0
    if issues >= 10 and closed / issues < 0.05 and forks <= 1:
        flags.append('unclosed_issue_pile')
    if record.get('clone_of'):
        flags.append('clone')

    multiplier = 1.0
    for flag in flags:
        multiplier *= PENALTIES[flag]
    return round(max(multiplier, MIN_INTEGRITY), 3), flags


def confidence(record: dict, now: datetime) -> float:
    """How much to trust the score: grows with the number of daily snapshots and the repository's age."""
    snapshots = sum(1 for value in record.get('stars') or [] if value is not None)
    has_readme = record['enrich']['readme']['bytes'] > 0
    return round(0.5 * min(1.0, snapshots / 7) + 0.2 + 0.1 * has_readme
                 + 0.2 * min(1.0, age_days(record['created_at'], now) / 14), 3)


def utility_component(record: dict) -> Optional[float]:
    """The model's 0-5 utility rating scaled to [0, 1], or None when there is no verified rating."""
    value = (record.get('class') or {}).get('utility')
    return None if value is None else value / 5


def total(components: Dict[str, Optional[float]], weights: Dict[str, float], relevance: float, integrity_mult: float) -> float:
    available = {name: weight for name, weight in weights.items() if components.get(name) is not None}
    if sum(available.values()) <= 0:
        return 0.0
    base = sum(weight * components[name] for name, weight in available.items()) / sum(available.values())
    return round(100 * relevance * integrity_mult * base, 1)


def score_all(records: List[dict], now: datetime, config: dict) -> int:
    """Score every scorable record in place (`record['score']`). Momentum is a percentile within the record's
    own cohort (emerging vs established) so a viral outlier cannot compress everyone else. Returns the count."""
    scoring = config['scoring']
    scorable = [r for r in records if is_scorable(r)]
    status = {r['id']: 'emerging' if is_emerging(r, now, config['window_days']) else 'established' for r in scorable}

    momentum = {}
    for cohort in ('emerging', 'established'):
        members = [r for r in scorable if status[r['id']] == cohort]
        for record, pct in zip(members, percentile_ranks([momentum_raw(r, now) for r in members])):
            momentum[record['id']] = pct

    for record in scorable:
        facts = record['enrich']
        stars, forks = state.latest(record.get('stars')), state.latest(record.get('forks'))
        components = {
            'momentum': momentum[record['id']],
            'activity': activity(facts, now),
            'maturity': maturity(record, facts),
            'community': community(facts, stars, forks),
            'utility': utility_component(record),
        }
        integrity_mult, flags = integrity(record, facts, stars, forks)
        relevance = scoring['relevance'][record['class']['scope']]
        conf = confidence(record, now)
        totals = {name: total(components, weights, relevance, integrity_mult)
                  for name, weights in scoring['profiles'].items()}
        record['score'] = {
            'at': now.date().isoformat(),
            'status': status[record['id']],
            'components': {name: None if value is None else round(value, 3) for name, value in components.items()},
            'integrity': {'mult': integrity_mult, 'flags': flags},
            'relevance': relevance,
            'confidence': conf,
            'totals': totals,
            'rank_keys': {name: round(value - scoring['confidence_penalty'] * (1 - conf), 1) for name, value in totals.items()},
            'star_rate': round(growth_rate(record.get('stars'), age_days(record['created_at'], now)), 3),
        }
    return len(scorable)
