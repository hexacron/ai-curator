"""Tracker configuration: code defaults overridden by the `osint` section of config.json."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # A repository is "emerging" while it was created within this many days.
    'window_days': 90,
    # GitHub topics swept for candidates. OSINT-specific only; broad ones (reconnaissance,
    # threat-intelligence, geolocation) would multiply the tracked set with mostly out-of-scope repos.
    'topics': [
        'osint', 'osint-tools', 'osint-tool', 'osint-framework', 'osint-python',
        'osint-resources', 'osint-reconnaissance', 'open-source-intelligence',
        'geoint', 'socmint', 'imint',
    ],
    # GitHub allows 30 search requests per minute.
    'search_delay_seconds': 2.2,
    'triage': {
        # Stars required grow with age: max(1, ceil(age_days / floor_days_per_star)).
        'floor_days_per_star': 15,
        # An owner with at least this many candidates in the window is flagged as a batch publisher.
        'batch_publisher_threshold': 5,
    },
    'retention': {
        # Candidates that never passed triage are dropped this long after leaving the window.
        'prune_grace_days': 7,
    },
    'enrich': {
        # Upper bound on first-commit REST lookups per run (one call per newly tracked repository).
        'max_history_lookups_per_run': 400,
    },
    'scoring': {
        # Weights per component. `utility` is filled by the LLM review stage; until then the others are renormalised.
        'profiles': {
            'rising': {'momentum': 0.45, 'activity': 0.20, 'maturity': 0.15, 'community': 0.10, 'utility': 0.10},
            'overall': {'momentum': 0.20, 'activity': 0.20, 'maturity': 0.25, 'community': 0.20, 'utility': 0.15},
        },
        # Score multiplier by scope; `out` is never scored.
        'relevance': {'core': 1.0, 'adjacent': 0.6},
        # rank_key = total - confidence_penalty * (1 - confidence)
        'confidence_penalty': 15,
        # Repositories whose integrity multiplier is below this are left off every board.
        'unverified_below': 0.7,
    },
    'boards': {
        'size': 25,
        'domain_size': 10,
        'owner_cap': 2,
        'min_confidence_rising': 0.5,
        'new_days': 7,
    },
    'llm': {
        # LLM review needs ANTHROPIC_API_KEY (Actions secret / .env). Without it the tracker runs rules-only.
        'enabled': True,
        'model': 'claude-haiku-5-5',
        # Asked once more when the first answer's confidence is below `escalate_below_confidence`; '' disables.
        'escalation_model': 'claude-sonnet-5-5',
        'escalate_below_confidence': 0.6,
        'effort': 'low',
        # Hard budget of API requests per run (escalations count); unreviewed repositories wait for the next run.
        'max_calls_per_run': 120,
        'max_tokens': 4000,
        'timeout_seconds': 90,
        # Below this the model changes nothing except raising sensitivity.
        'min_confidence_to_override': 0.5,
        # Repositories the rules reject at or above this confidence are not sent to the model.
        'review_skip_out_confidence': 0.5,
    },
    'privacy': {
        # state.jsonl is public. For `high`-sensitivity repositories keep only identity and counters: drop the
        # description, topics, homepage and any model-written text.
        'redact_high_sensitivity': True,
    },
}


def load_config(path: str = 'config.json') -> Dict[str, Any]:
    """Defaults merged with config.json's `osint` section. Raises on malformed JSON or unknown keys,
    because silently falling back would make today's snapshots incomparable with yesterday's."""
    config = copy.deepcopy(DEFAULTS)

    if Path(path).exists():
        with open(path, 'r', encoding='utf-8') as f:
            try:
                section = json.load(f).get('osint', {})
            except json.JSONDecodeError as e:
                raise ValueError(f"Could not decode {path}: {e}")
        if not isinstance(section, dict):
            raise ValueError(f"'osint' in {path} must be an object")
        _merge(config, section, 'osint')

    _validate(config)
    return config


def _merge(base: Dict[str, Any], override: Dict[str, Any], where: str) -> None:
    for key, value in override.items():
        if key not in base:
            raise ValueError(f"Unknown setting '{where}.{key}'")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"'{where}.{key}' must be an object")
            _merge(base[key], value, f"{where}.{key}")
        else:
            base[key] = value


def _validate(config: Dict[str, Any]) -> None:
    if not isinstance(config['window_days'], int) or config['window_days'] < 1:
        raise ValueError("osint.window_days must be a positive integer")
    topics = config['topics']
    if not isinstance(topics, list) or not topics or not all(isinstance(t, str) and t.strip() for t in topics):
        raise ValueError("osint.topics must be a non-empty list of topic names")
    config['topics'] = list(dict.fromkeys(t.strip().lower() for t in topics))
    if config['search_delay_seconds'] < 0:
        raise ValueError("osint.search_delay_seconds must not be negative")
    if config['triage']['floor_days_per_star'] <= 0:
        raise ValueError("osint.triage.floor_days_per_star must be positive")
    if config['triage']['batch_publisher_threshold'] < 1:
        raise ValueError("osint.triage.batch_publisher_threshold must be at least 1")
    if config['retention']['prune_grace_days'] < 0:
        raise ValueError("osint.retention.prune_grace_days must not be negative")
    scoring = config['scoring']
    for name, weights in scoring['profiles'].items():
        if any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
            raise ValueError(f"osint.scoring.profiles.{name} needs non-negative weights with a positive sum")
    if any(not 0 <= m <= 1 for m in scoring['relevance'].values()):
        raise ValueError("osint.scoring.relevance multipliers must be between 0 and 1")
    if not 0 <= scoring['unverified_below'] <= 1:
        raise ValueError("osint.scoring.unverified_below must be between 0 and 1")
    if scoring['confidence_penalty'] < 0:
        raise ValueError("osint.scoring.confidence_penalty must not be negative")
    boards = config['boards']
    if min(boards['size'], boards['domain_size'], boards['owner_cap']) < 1:
        raise ValueError("osint.boards.size, domain_size and owner_cap must be at least 1")
    if config['enrich']['max_history_lookups_per_run'] < 0:
        raise ValueError("osint.enrich.max_history_lookups_per_run must not be negative")
    llm = config['llm']
    if not isinstance(llm['model'], str) or not llm['model'].strip():
        raise ValueError("osint.llm.model must be a model id")
    if llm['effort'] not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError("osint.llm.effort must be one of low, medium, high, xhigh, max")
    if llm['max_calls_per_run'] < 0 or llm['max_tokens'] < 1 or llm['timeout_seconds'] <= 0:
        raise ValueError("osint.llm.max_calls_per_run, max_tokens and timeout_seconds must be positive")
    for name in ('escalate_below_confidence', 'min_confidence_to_override', 'review_skip_out_confidence'):
        if not 0 <= llm[name] <= 1:
            raise ValueError(f"osint.llm.{name} must be between 0 and 1")
