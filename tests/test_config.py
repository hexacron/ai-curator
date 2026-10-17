import json

import pytest

from osint_tracker.config import load_config


def load(tmp_path, osint):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'osint': osint}))
    return load_config(str(path))


def test_llm_and_privacy_defaults_are_valid_and_overridable(tmp_path):
    config = load(tmp_path, {'llm': {'model': 'claude-sonnet-5-5', 'max_calls_per_run': 10}, 'privacy': {'redact_high_sensitivity': False}})

    assert config['llm']['model'] == 'claude-sonnet-5-5' and config['llm']['max_calls_per_run'] == 10
    assert config['llm']['escalation_model'] == 'claude-sonnet-5-5' and config['privacy']['redact_high_sensitivity'] is False
    assert load_config('does-not-exist.json')['privacy']['redact_high_sensitivity'] is True


@pytest.mark.parametrize('llm', [
    {'effort': 'turbo'}, {'model': ''}, {'max_calls_per_run': -1}, {'min_confidence_to_override': 1.5},
    {'timeout_seconds': 0}, {'unknown_key': 1},
])
def test_bad_llm_settings_raise(tmp_path, llm):
    with pytest.raises(ValueError):
        load(tmp_path, {'llm': llm})


@pytest.mark.parametrize('value', [-1, 'two weeks', 1.5])
def test_a_bad_first_commit_grace_raises(tmp_path, value):
    with pytest.raises(ValueError):
        load(tmp_path, {'first_commit_grace_days': value})


def test_the_grace_period_defaults_to_two_weeks_and_can_be_overridden(tmp_path):
    assert load_config('does-not-exist.json')['first_commit_grace_days'] == 14
    assert load(tmp_path, {'first_commit_grace_days': 0})['first_commit_grace_days'] == 0
