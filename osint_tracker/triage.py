"""Tier-1 triage: cheap rules over fields the search response already carries (no extra API calls)."""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Dict, Iterable

from osint_tracker.clock import age_days


def required_stars(age: float, days_per_star: float) -> int:
    """Stars needed to pass triage. The floor grows with age so a week-old repository with a single star
    survives, but one that has attracted almost no attention after two months does not."""
    return max(1, math.ceil(age / days_per_star))


def triage(items: Iterable[dict], now: datetime, settings: dict) -> Dict[int, dict]:
    """Per repository id: `tier1` (clears the age-scaled star floor) and `batch_publisher`
    (its owner has many candidates in the window; a flag for later review, never a filter)."""
    items = list(items)
    per_owner = Counter(item['owner']['login'].lower() for item in items)

    verdicts: Dict[int, dict] = {}
    for item in items:
        needed = required_stars(age_days(item['created_at'], now), settings['floor_days_per_star'])
        verdicts[item['id']] = {
            'tier1': item['stargazers_count'] >= needed,
            'batch_publisher': per_owner[item['owner']['login'].lower()] >= settings['batch_publisher_threshold'],
        }
    return verdicts
