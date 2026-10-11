"""Persistent tracker state: one JSON object per repository, one line each, in <data_dir>/state.jsonl.

Line 1 is a `_meta` header; every other line is a repository record keyed by its numeric GitHub id
(stable across renames and transfers). Star and fork history lives in the record as day-indexed
arrays starting at `snap_start`, with null for days the repository was not observed.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List

from osint_tracker.clock import age_days

STATE_VERSION = 1
STATE_FILENAME = 'state.jsonl'

Repos = Dict[int, dict]


def state_path(data_dir: str) -> Path:
    return Path(data_dir) / STATE_FILENAME


def load(data_dir: str) -> Repos:
    """Load all records. A missing file is an empty state; an unreadable one raises, because
    carrying on would overwrite accumulated history that cannot be re-collected."""
    path = state_path(data_dir)
    if not path.exists():
        return {}

    repos: Repos = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            meta = json.loads(f.readline()).get('_meta')
            if not meta or meta.get('version') != STATE_VERSION:
                raise ValueError(f"unsupported state header {meta!r}")
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    repos[record['id']] = record
    except (ValueError, KeyError, AttributeError) as e:
        raise ValueError(f"Cannot read tracker state {path}: {e}") from e
    return repos


def save(data_dir: str, repos: Repos, now: datetime) -> Path:
    """Atomically replace the state file (sorted by id so day-to-day diffs stay small)."""
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        header = {'_meta': {'version': STATE_VERSION, 'updated': now.isoformat(timespec='seconds')}}
        f.write(json.dumps(header, separators=(',', ':')) + '\n')
        for repo_id in sorted(repos):
            f.write(json.dumps(repos[repo_id], sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n')
    os.replace(tmp, path)
    return path


def record_snapshot(record: dict, day: date, stars: int, forks: int) -> None:
    """Store one day's counters. Calling it again for the same day overwrites, so reruns are idempotent."""
    if not record.get('snap_start'):
        record['snap_start'] = day.isoformat()
        record['stars'] = []
        record['forks'] = []

    index = (day - date.fromisoformat(record['snap_start'])).days
    if index < 0:
        raise ValueError(f"Snapshot day {day} precedes series start {record['snap_start']} for repo {record['id']}")

    for series, value in ((record['stars'], stars), (record['forks'], forks)):
        if len(series) <= index:
            series.extend([None] * (index + 1 - len(series)))
        series[index] = value


def prune(repos: Repos, now: datetime, window_days: int, grace_days: int) -> List[int]:
    """Drop records that left the window without ever passing triage. Tier-1 records are kept for good."""
    removed = [
        repo_id for repo_id, record in repos.items()
        if not record.get('tier1') and age_days(record['created_at'], now) > window_days + grace_days
    ]
    for repo_id in removed:
        del repos[repo_id]
    return removed


def latest(series: list) -> int:
    """Most recent recorded value of a day-indexed series (0 if nothing was ever recorded)."""
    return next((value for value in reversed(series or []) if value is not None), 0)
