import time
from types import SimpleNamespace

import pytest

from github_client import MAX_RATE_LIMIT_WAIT_SECONDS, GitHubAPIError, GitHubClient
from osint_tracker.config import load_config


def response(status, headers=None, text=''):
    return SimpleNamespace(status_code=status, headers=headers or {}, text=text)


def test_server_errors_back_off_exponentially():
    assert GitHubClient.retry_delay(response(502), 0) == 2.0
    assert GitHubClient.retry_delay(response(502), 2) == 8.0


def test_rate_limit_honours_retry_after_then_reset_time():
    assert GitHubClient.retry_delay(response(403, {'Retry-After': '30'}), 0) == 30.0

    reset = str(int(time.time()) + 10)
    wait = GitHubClient.retry_delay(response(403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': reset}), 0)
    assert 10 <= wait <= 12


def test_waits_longer_than_the_cap_are_not_retried():
    assert GitHubClient.retry_delay(response(429, {'Retry-After': str(MAX_RATE_LIMIT_WAIT_SECONDS + 1)}), 0) is None


def test_secondary_rate_limit_without_hints_backs_off_but_permission_errors_do_not_retry():
    assert GitHubClient.retry_delay(response(403, text='You have exceeded a secondary rate limit'), 0) == 10.0
    assert GitHubClient.retry_delay(response(403, text='Resource not accessible by integration'), 0) is None
    assert GitHubClient.retry_delay(response(404), 0) is None


def test_graphql_tolerates_missing_nodes_but_raises_on_real_errors(monkeypatch):
    client = GitHubClient('token')

    monkeypatch.setattr(client, '_request_json', lambda *a, **k: {
        'data': {'nodes': [None]}, 'errors': [{'type': 'NOT_FOUND', 'message': 'gone'}]})
    assert client.graphql('query') == {'nodes': [None]}

    monkeypatch.setattr(client, '_request_json', lambda *a, **k: {
        'data': {'nodes': []}, 'errors': [{'type': 'RATE_LIMITED', 'message': 'slow down'}]})
    with pytest.raises(GitHubAPIError):
        client.graphql('query')


def test_config_overrides_merge_but_typos_and_bad_json_fail_loudly(tmp_path):
    good = tmp_path / 'good.json'
    good.write_text('{"osint": {"window_days": 30, "topics": ["OSINT", "osint", "geoint"], "triage": {"floor_days_per_star": 10}}}')
    config = load_config(str(good))
    assert config['window_days'] == 30
    assert config['topics'] == ['osint', 'geoint']
    assert config['triage'] == {'floor_days_per_star': 10, 'batch_publisher_threshold': 5}

    typo = tmp_path / 'typo.json'
    typo.write_text('{"osint": {"window_day": 30}}')
    with pytest.raises(ValueError):
        load_config(str(typo))

    broken = tmp_path / 'broken.json'
    broken.write_text('{"osint": ')
    with pytest.raises(ValueError):
        load_config(str(broken))
