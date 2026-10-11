"""Identity resolution: copies of the same project published under different owners.

`fork:false` does not catch these (a copy pasted into a fresh repository is not a fork), so they are found
by identical README content or by the same repository name with the same description."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable

from osint_tracker import state

MIN_README_BYTES = 1500       # shorter READMEs are too generic to prove a copy
MIN_DESCRIPTION_CHARS = 30


def find_clones(records: Iterable[dict]) -> Dict[int, int]:
    """For every repository that duplicates an earlier one: {its id: id of the original}. The original is the
    earliest-created member of the group (ties: most stars, then lowest id) and is not listed itself."""
    records = list(records)
    by_id = {record['id']: record for record in records}

    groups = defaultdict(list)
    for record in records:
        readme = (record.get('enrich') or {}).get('readme') or {}
        if readme.get('sha256') and (readme.get('bytes') or 0) >= MIN_README_BYTES:
            groups[('readme', readme['sha256'])].append(record)
        description = ' '.join((record.get('description') or '').lower().split())
        if len(description) >= MIN_DESCRIPTION_CHARS:
            groups[('name', record['full_name'].split('/')[1].lower(), description)].append(record)

    def oldest(members):
        return min(members, key=lambda r: (r['created_at'], -state.latest(r.get('stars')), r['id']))

    parent: Dict[int, int] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        original = oldest(members)
        for member in members:
            if member is original:
                continue
            current = parent.get(member['id'])
            if current is None or original['created_at'] < by_id[current]['created_at']:
                parent[member['id']] = original['id']

    def root(repo_id: int) -> int:
        seen = set()
        while repo_id in parent and repo_id not in seen:
            seen.add(repo_id)
            repo_id = parent[repo_id]
        return repo_id

    return {repo_id: root(repo_id) for repo_id in parent if root(repo_id) != repo_id}
