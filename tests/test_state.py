from datetime import date

import pytest

from osint_tracker import state
from tests.helpers import NOW


def test_snapshots_are_day_indexed_with_gaps_and_same_day_reruns_overwrite():
    record = {'id': 1}

    state.record_snapshot(record, date(2026, 10, 1), 5, 1)
    state.record_snapshot(record, date(2026, 10, 4), 9, 2)
    state.record_snapshot(record, date(2026, 10, 4), 11, 3)  # rerun on the same day

    assert record['snap_start'] == '2026-10-01'
    assert record['stars'] == [5, None, None, 11]
    assert record['forks'] == [1, None, None, 3]


def test_a_snapshot_before_the_series_start_is_rejected():
    record = {'id': 1}
    state.record_snapshot(record, date(2026, 10, 5), 5, 0)

    with pytest.raises(ValueError):
        state.record_snapshot(record, date(2026, 10, 4), 5, 0)


def test_state_round_trips_and_leaves_no_temp_file(tmp_path):
    repos = {7: {'id': 7, 'description': 'Поиск по нику 🔍', 'stars': [1, None, 3]},
             2: {'id': 2, 'description': None}}

    state.save(str(tmp_path), repos, NOW)

    assert state.load(str(tmp_path)) == repos
    assert [p.name for p in tmp_path.iterdir()] == ['state.jsonl']


def test_a_corrupt_state_file_raises_instead_of_looking_empty(tmp_path):
    (tmp_path / 'state.jsonl').write_text('{"_meta":{"version":1}}\n{"id": 1, "trunc')

    with pytest.raises(ValueError):
        state.load(str(tmp_path))


def test_prune_drops_untriaged_repos_after_the_grace_period_but_never_tier1():
    def record(repo_id, age_days, tier1):
        from datetime import timedelta
        created = (NOW - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {'id': repo_id, 'created_at': created, 'tier1': tier1}

    repos = {1: record(1, 96, False), 2: record(2, 98, False), 3: record(3, 400, True)}

    removed = state.prune(repos, NOW, window_days=90, grace_days=7)

    assert removed == [2]
    assert sorted(repos) == [1, 3]
