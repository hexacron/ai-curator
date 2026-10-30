"""One collection run: discover candidates, triage, snapshot, enrich the tier-1 set, classify (rules, then LLM
review), score and rank."""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import text_digest as digest
from github_client import GitHubAPIError, GitHubClient
from osint_tracker import classify, curation, discover, enrich, identify, rank, review, score, state, triage

logger = logging.getLogger(__name__)


def collect(client: GitHubClient, config: dict, data_dir: str, now: Optional[datetime] = None,
            dry_run: bool = False, sleep: Callable[[float], None] = time.sleep, llm=None) -> Dict[str, int]:
    """Run one collection and return counters. `llm` is an AnthropicClient (or None for rules-only).
    Nothing is written if any GitHub call fails or discovery is empty, so a bad run can never replace good
    history with a hole; LLM failures only leave repositories unreviewed until the next run."""
    now = now or datetime.now(timezone.utc)
    today = now.date()

    items = discover.discover(client, config['topics'], now - timedelta(days=config['window_days']), now,
                              config['search_delay_seconds'], sleep)
    if not items:
        raise GitHubAPIError("No candidates discovered; refusing to record an empty day")

    repos = state.load(data_dir)
    verdicts = triage.triage(items.values(), now, config['triage'])

    new = 0
    for repo_id, item in items.items():
        record = repos.get(repo_id)
        if record is None:
            record = repos[repo_id] = {'id': repo_id, 'first_seen': today.isoformat(), 'names': []}
            new += 1
        _apply_search_item(record, item, today.isoformat())
        record.update(verdicts[repo_id])
        state.record_snapshot(record, today, item['stargazers_count'], item['forks_count'])

    counts, work = _enrich_tracked(client, repos, set(items), now, config)
    review_counts = _classify_tracked(work, llm, config, curation.load(data_dir), today)

    tracked = [r for r in repos.values() if r.get('tier1') and not r.get('gone')]
    clones = identify.find_clones(r for r in tracked if r.get('enrich'))
    for record in tracked:
        record['clone_of'] = clones.get(record['id'])
    scored = score.score_all(tracked, now, config)

    boards = rank.build_boards(tracked, now, config, rank.load_previous_ranks(data_dir))
    boards['stats'] = {'candidates': len(items), 'tracked': len(tracked),
                       'reviewed': sum(1 for r in tracked if (r.get('class') or {}).get('reviewed'))}
    pruned = state.prune(repos, now, config['window_days'], config['retention']['prune_grace_days'])

    if config['privacy']['redact_high_sensitivity']:
        _redact_high_sensitivity(repos)
    if not dry_run:
        state.save(data_dir, repos, now)
        rank.save(data_dir, boards)

    for entry in boards['boards']['rising'][:5]:
        logger.info(f"rising #{entry['rank']}: {entry['full_name']} score={entry['score']} "
                    f"stars={entry['stars']} domain={entry['domain']}")

    scopes = Counter(r['class']['scope'] for r in tracked if r.get('class'))
    return {
        'discovered': len(items),
        'new': new,
        'tier1': sum(1 for repo_id in items if repos[repo_id]['tier1']),
        'batch_publisher': sum(1 for repo_id in items if repos[repo_id]['batch_publisher']),
        'enriched': counts['enriched'],
        'graduates_refreshed': counts['graduates_refreshed'],
        'history_lookups': counts['history_lookups'],
        'gone': counts['gone'],
        'clones': len(clones),
        'scope_core': scopes['core'],
        'scope_adjacent': scopes['adjacent'],
        'scope_out': scopes['out'],
        'reviewed': review_counts['reviewed'],
        'review_cached': review_counts['cached'],
        'review_pending': review_counts['pending'],
        'review_failed': review_counts['failed'],
        'llm_calls': review_counts['calls'],
        'llm_tokens_in': review_counts['tokens_in'],
        'llm_tokens_out': review_counts['tokens_out'],
        'scored': scored,
        'rising': len(boards['boards']['rising']),
        'pruned': len(pruned),
        'tracked': len(repos),
    }


def _enrich_tracked(client: GitHubClient, repos: state.Repos, discovered: set, now: datetime, config: dict):
    """Enrich every tier-1 repository with one batched GraphQL lookup by node id and run the rules over it.
    Tier-1 repositories absent from today's discovery (aged past the window, archived, ...) also get their
    snapshot from that lookup, so their history keeps growing; deleted or private ones are marked `gone`.
    Returns (counters, work) where `work` holds each repository's rules classification and review text."""
    targets = [r for r in repos.values() if r.get('tier1') and not r.get('gone')]
    counts: Counter = Counter()
    work: List[dict] = []
    if not targets:
        return counts, work

    today = now.date()
    nodes = enrich.fetch(client, [r['node_id'] for r in targets], now)
    lookups_left = config['enrich']['max_history_lookups_per_run']

    for record in targets:
        node = nodes[record['node_id']]
        if node is None:
            if record['id'] not in discovered:
                record['gone'] = True
                counts['gone'] += 1
            continue

        if record['id'] not in discovered:
            _set_full_name(record, node['name'])
            record.update({
                'owner': node['name'].split('/')[0],
                'html_url': node['url'],
                'description': node['description'],
                'pushed_at': node['pushed_at'],
                'archived': node['archived'],
                'last_seen': today.isoformat(),
            })
            state.record_snapshot(record, today, node['stars'], node['forks'])
            counts['graduates_refreshed'] += 1

        facts = node['facts']
        record['enrich'] = {'at': today.isoformat(), **facts}
        if record.get('earliest_commit') is None and lookups_left > 0:
            lookups_left -= 1
            record['earliest_commit'] = enrich.earliest_commit(client, record['full_name'], facts['commits_total'])
            counts['history_lookups'] += 1

        rules = classify.classify(record['full_name'].split('/')[1], record.get('description'),
                                  record.get('topics') or [], node['readme_text'])
        block = review.data_block(record, digest.build(node['readme_text'])) if config['llm']['enabled'] else ''
        work.append({'record': record, 'rules': rules, 'block': block})
        counts['enriched'] += 1
    return counts, work


def _classify_tracked(work: List[dict], llm, config: dict, curated: curation.Curation, today) -> Counter:
    """Review what needs it, then give every repository its final classification: the rules, adjusted by the
    stored review (stale ones still apply until replaced, so classifications do not flap when the call budget
    runs out), adjusted by human curation."""
    settings = config['llm']
    counts = review.run_reviews(work, llm, settings, today) if settings['enabled'] else Counter()
    for item in work:
        record = item['record']
        stored = record.get('review') if settings['enabled'] else None
        merged = review.merge(item['rules'], stored['result'] if stored else None, settings)
        record['class'] = curated.apply(record, merged)
    return counts


def _redact_high_sensitivity(repos: state.Repos) -> None:
    """state.jsonl is public. For repositories of `high` sensitivity keep only identity, labels and counters:
    no description, topics, homepage or model-written text. (Their owner/name remains: it is the identity the
    history is keyed to.) Repositories that never got past triage have no classification, so the rules over
    their description decide. Lookup-bot accounts publish many near-identical repositories whose wording the
    rules do not all recognise, so the other repositories of an owner with a `high` repository are redacted
    too, except ones that are publishable (core or adjacent and not `high`): their text is shown on the site
    anyway, and wiping their stored model review would cost the summary for good. Redacted reviews carry
    `redacted: True` so they are not mistaken for damaged ones. The next run repopulates these fields in
    memory and redacts again."""
    high_ids, high_owners = set(), set()
    for record in repos.values():
        klass = record.get('class')
        if klass is not None:
            high = klass.get('sensitivity') == 'high'
        else:
            rules = classify.classify(record['full_name'].split('/')[1], record.get('description'),
                                      record.get('topics') or [], None)
            high = rules['sensitivity'] == 'high'
        if high:
            high_ids.add(record['id'])
            high_owners.add(record['owner'].lower())

    def publishable(record: dict) -> bool:
        klass = record.get('class')
        return klass is not None and klass['scope'] in ('core', 'adjacent') and klass['sensitivity'] != 'high'

    for record in repos.values():
        if record['id'] not in high_ids and (record['owner'].lower() not in high_owners or publishable(record)):
            continue
        record.update({'description': None, 'topics': [], 'homepage': None})
        klass = record.get('class')
        if klass is not None:
            klass.update({'summary': None, 'strengths': [], 'risks': [], 'llm_reason': None})
        result = (record.get('review') or {}).get('result')
        if result:
            result.update({'summary': '', 'scope_reason': '', 'strengths': [], 'risks': [], 'utility_evidence': [],
                           'redacted': True})


def _set_full_name(record: dict, full_name: str) -> None:
    """Assign the current name, remembering earlier ones (the numeric id is the identity, names change)."""
    previous = record.get('full_name')
    if previous and previous != full_name and previous not in record['names']:
        record['names'].append(previous)
    record['full_name'] = full_name


def _apply_search_item(record: dict, item: dict, seen_on: str) -> None:
    _set_full_name(record, item['full_name'])
    license_info = item.get('license') or {}
    record.update({
        'node_id': item['node_id'],
        'owner': item['owner']['login'],
        'owner_type': item['owner']['type'],
        'html_url': item['html_url'],
        'description': item.get('description'),
        'language': item.get('language'),
        'topics': item.get('topics') or [],
        'license': license_info.get('spdx_id') or license_info.get('name'),
        'homepage': item.get('homepage') or None,
        'created_at': item['created_at'],
        'pushed_at': item.get('pushed_at'),
        'size_kb': item.get('size') or 0,
        'open_issues': item.get('open_issues_count') or 0,
        'archived': bool(item.get('archived')),
        'last_seen': seen_on,
        'gone': False,
    })
