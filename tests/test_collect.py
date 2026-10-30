import copy
import json
from datetime import timedelta

import pytest

from github_client import GitHubAPIError
from osint_tracker import state
from osint_tracker.collect import collect
from osint_tracker.config import load_config
from llm_client import LLMError
from tests.helpers import NOW, VALID_REPLY, FakeGitHub, FakeLLM, days_ago, make_repo, ts

CONFIG = load_config('does-not-exist.json')
CONFIG['search_delay_seconds'] = 0


def run(fake, data_dir, now=NOW, **kwargs):
    return collect(fake, CONFIG, str(data_dir), now=now, sleep=lambda s: None, **kwargs)


def test_first_run_records_a_snapshot_and_triage_verdicts(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, forks=3), make_repo(2, days_ago(40), stars=1)])

    summary = run(fake, tmp_path)

    repos = state.load(str(tmp_path))
    assert (summary['discovered'], summary['new'], summary['tier1']) == (2, 2, 1)
    assert repos[1]['tier1'] is True and repos[2]['tier1'] is False
    assert (repos[1]['snap_start'], repos[1]['stars'], repos[1]['forks']) == ('2026-10-10', [20], [3])
    assert repos[1]['first_seen'] == '2026-10-10'


def test_rerunning_the_same_day_is_idempotent(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run(fake, tmp_path)
    first = (tmp_path / 'state.jsonl').read_text().splitlines()[1:]

    summary = run(fake, tmp_path)

    assert summary['new'] == 0
    assert (tmp_path / 'state.jsonl').read_text().splitlines()[1:] == first


def test_later_runs_append_to_the_series_and_skipped_days_become_gaps(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run(fake, tmp_path)
    fake.repos[1]['stargazers_count'] = 35

    run(fake, tmp_path, now=NOW + timedelta(days=3))

    record = state.load(str(tmp_path))[1]
    assert record['stars'] == [20, None, None, 35]
    assert record['first_seen'] == '2026-10-10'


def test_a_renamed_repo_keeps_its_identity_and_remembers_the_old_name(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run(fake, tmp_path)
    fake.repos[1]['full_name'] = 'alice/better-name'

    run(fake, tmp_path, now=NOW + timedelta(days=1))

    repos = state.load(str(tmp_path))
    assert list(repos) == [1]
    assert repos[1]['full_name'] == 'alice/better-name'
    assert repos[1]['names'] == ['alice/repo1']


def test_graduates_keep_growing_after_leaving_the_window_while_untriaged_repos_are_pruned(tmp_path):
    fake = FakeGitHub([
        make_repo(1, days_ago(19), stars=10),  # clears the floor (needs 2)
        make_repo(2, days_ago(19), stars=1),   # does not
    ])
    run(fake, tmp_path)
    fake.repos[1]['stargazers_count'] = 400
    fake.repos[1]['forks_count'] = 40

    later = NOW + timedelta(days=82)  # repo 1 is now 101 days old: outside the 90-day window
    fake.add(make_repo(3, days_ago(5, now=later)))  # discovery still finds fresh repos, as it always does in practice
    summary = run(fake, tmp_path, now=later)

    repos = state.load(str(tmp_path))
    assert (summary['discovered'], summary['graduates_refreshed'], summary['pruned']) == (1, 1, 1)
    assert 2 not in repos
    assert repos[1]['stars'][0] == 10 and repos[1]['stars'][-1] == 400
    assert repos[1]['forks'][-1] == 40


def test_a_deleted_graduate_is_marked_gone_and_kept(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(19), stars=10)])
    run(fake, tmp_path)
    fake.repos[1]['deleted'] = True

    fake.add(make_repo(3, days_ago(5, now=NOW + timedelta(days=82))))
    summary = run(fake, tmp_path, now=NOW + timedelta(days=82))

    record = state.load(str(tmp_path))[1]
    assert summary['gone'] == 1
    assert record['gone'] is True and record['stars'] == [10]


def test_empty_discovery_raises_and_writes_nothing(tmp_path):
    with pytest.raises(GitHubAPIError):
        run(FakeGitHub([]), tmp_path)

    assert not (tmp_path / 'state.jsonl').exists()


def test_an_api_failure_mid_run_leaves_existing_state_untouched(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, topics=('osint', 'geoint'))])
    run(fake, tmp_path)
    before = (tmp_path / 'state.jsonl').read_bytes()
    fake.repos[1]['stargazers_count'] = 99
    fake.calls, fake.fail_on_call = 0, 3  # dies partway through the topic sweep

    with pytest.raises(GitHubAPIError):
        run(fake, tmp_path, now=NOW + timedelta(days=1))

    assert (tmp_path / 'state.jsonl').read_bytes() == before


def test_dry_run_writes_nothing(tmp_path):
    run(FakeGitHub([make_repo(1, days_ago(40))]), tmp_path, dry_run=True)

    assert not (tmp_path / 'state.jsonl').exists()


def boards(data_dir):
    return json.loads((data_dir / 'boards.json').read_text(encoding='utf-8'))


def test_a_run_enriches_classifies_scores_and_publishes_boards(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])

    summary = run(fake, tmp_path)

    record = state.load(str(tmp_path))[1]
    assert record['enrich']['readme']['checks']['install'] is True
    assert (record['class']['scope'], record['class']['domain']) == ('core', 'geospatial-imagery')
    assert record['score']['status'] == 'emerging'
    assert record['earliest_commit'] == record['created_at']
    assert [e['id'] for e in boards(tmp_path)['boards']['rising']] == [1]
    assert (summary['enriched'], summary['scored'], summary['rising']) == (1, 1, 1)


def test_the_first_commit_is_looked_up_once_per_repository(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])

    run(fake, tmp_path)
    run(fake, tmp_path, now=NOW + timedelta(days=1))

    assert fake.commit_lookups == 1


def test_a_repo_whose_history_predates_the_window_is_established_not_rising(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, earliest_commit=ts(days_ago(400)))])

    run(fake, tmp_path)

    published = boards(tmp_path)['boards']
    assert [e['id'] for e in published['rising']] == []
    assert [e['id'] for e in published['established']] == [1]


def test_sensitive_and_out_of_scope_repos_are_tracked_but_never_published(tmp_path):
    fake = FakeGitHub([
        make_repo(1, days_ago(40), stars=20, description='Пробив по номеру телефона: поиск по базам'),
        make_repo(2, days_ago(40), stars=20, description='Phishing framework and payload generator for red team'),
        make_repo(3, days_ago(40), stars=20),
    ])

    run(fake, tmp_path)

    repos, published = state.load(str(tmp_path)), boards(tmp_path)
    assert repos[1]['class']['sensitivity'] == 'high' and repos[2]['class']['scope'] == 'out'
    assert [e['id'] for e in published['boards']['rising']] == [3]
    assert [(r['id'], r['reason']) for r in published['rejected']] == [(2, 'out of scope')]
    assert published['excluded']['sensitivity_high_hidden'] == 1


def test_a_copy_of_an_earlier_repo_is_excluded_and_reported(tmp_path):
    text = '# Photo geolocator\n\n' + 'Finds where a photo was taken from EXIF data. ' * 60
    fake = FakeGitHub([make_repo(1, days_ago(50), stars=30, readme=text),
                       make_repo(2, days_ago(20), stars=30, owner='mallory', readme=text)])

    summary = run(fake, tmp_path)

    published = boards(tmp_path)
    assert summary['clones'] == 1 and state.load(str(tmp_path))[2]['clone_of'] == 1
    assert [e['id'] for e in published['boards']['rising']] == [1]
    assert (2, 'clone') in [(r['id'], r['reason']) for r in published['rejected']]


def test_rank_change_compares_with_the_previous_days_boards(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, forks=2), make_repo(2, days_ago(40), stars=200, forks=20)])
    run(fake, tmp_path)
    assert [e['id'] for e in boards(tmp_path)['boards']['rising']] == [2, 1]
    assert all(e['rank_change'] is None for e in boards(tmp_path)['boards']['rising'])

    fake.repos[1]['stargazers_count'], fake.repos[1]['forks_count'] = 5000, 1000  # repo 1 explodes overnight
    run(fake, tmp_path, now=NOW + timedelta(days=1))

    rising = boards(tmp_path)['boards']['rising']
    assert [(e['id'], e['rank'], e['rank_change']) for e in rising] == [(1, 1, 1), (2, 2, -1)]


# --- LLM review, curation and privacy ------------------------------------------------------------------

def run_llm(fake, data_dir, llm=None, config=CONFIG, now=NOW):
    return collect(fake, config, str(data_dir), now=now, sleep=lambda s: None, llm=llm)


def test_llm_review_is_stored_merged_scored_and_published(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    llm = FakeLLM()

    summary = run_llm(fake, tmp_path, llm)

    record = state.load(str(tmp_path))[1]
    assert (summary['reviewed'], summary['llm_calls'], summary['llm_tokens_in']) == (1, 1, 1000)
    assert record['review']['result']['summary'] == VALID_REPLY['summary']
    assert record['class']['reviewed'] is True and record['class']['utility'] == 4
    assert record['score']['components']['utility'] == pytest.approx(0.8)
    entry = boards(tmp_path)['boards']['rising'][0]
    assert entry['summary'] == VALID_REPLY['summary'] and entry['reviewed'] is True


def test_unchanged_repositories_are_not_reviewed_again(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    llm = FakeLLM()
    run_llm(fake, tmp_path, llm)

    summary = run_llm(fake, tmp_path, llm, now=NOW + timedelta(days=1))

    assert len(llm.calls) == 1
    assert (summary['review_cached'], summary['llm_calls'], summary['reviewed']) == (1, 0, 0)


def test_stored_reviews_keep_applying_when_no_client_is_available(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run_llm(fake, tmp_path, FakeLLM())

    summary = run_llm(fake, tmp_path, None, now=NOW + timedelta(days=1))

    assert state.load(str(tmp_path))[1]['class']['reviewed'] is True and summary['review_pending'] == 0


def test_a_new_repository_without_a_client_stays_unreviewed_and_is_counted_pending(tmp_path):
    summary = run_llm(FakeGitHub([make_repo(1, days_ago(40), stars=20)]), tmp_path, None)

    record = state.load(str(tmp_path))[1]
    assert record['class']['reviewed'] is False and 'review' not in record
    assert summary['review_pending'] == 1 and summary['llm_calls'] == 0


def test_disabling_llm_review_returns_to_rules_only(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run_llm(fake, tmp_path, FakeLLM())
    off = copy.deepcopy(CONFIG)
    off['llm']['enabled'] = False

    run_llm(fake, tmp_path, FakeLLM(), config=off, now=NOW + timedelta(days=1))

    klass = state.load(str(tmp_path))[1]['class']
    assert klass['reviewed'] is False and klass['summary'] is None and klass['utility'] is None


def test_llm_outages_do_not_fail_the_run_or_cost_the_day_s_data(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])

    summary = run_llm(fake, tmp_path, FakeLLM(error=LLMError('service down')))

    record = state.load(str(tmp_path))[1]
    assert summary['review_failed'] == 1 and record['class']['reviewed'] is False
    assert record['stars'] == [20]
    assert [e['id'] for e in boards(tmp_path)['boards']['rising']] == [1]


def test_the_model_can_take_a_repository_off_the_boards_but_never_lift_a_rejected_one(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20),
                       make_repo(2, days_ago(40), stars=20, description='Phishing framework and payload generator for red team')])
    llm = FakeLLM(reply={**VALID_REPLY, 'scope': 'out', 'domain': 'none', 'confidence': 0.9})

    run_llm(fake, tmp_path, llm)

    published = boards(tmp_path)
    assert [e['id'] for e in published['boards']['rising']] == []
    assert {(r['id'], r['reason']) for r in published['rejected']} == {(1, 'out of scope'), (2, 'out of scope')}
    assert len(llm.calls) == 1  # repo 2 was rejected by the rules with confidence and never sent


def test_curation_overrides_both_rules_and_model(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20), make_repo(2, days_ago(40), stars=20)])
    (tmp_path / 'curation.json').write_text(json.dumps({'block': ['alice/repo1'], 'pin': {'alice/repo2': {'domain': 'dark-web'}}}))

    run_llm(fake, tmp_path, FakeLLM())

    repos = state.load(str(tmp_path))
    assert repos[1]['class']['scope'] == 'out' and repos[1]['class']['source'] == 'curation'
    assert repos[2]['class']['domain'] == 'dark-web'
    assert [e['id'] for e in boards(tmp_path)['boards']['domains']['dark-web']] == [2]


def test_high_sensitivity_text_never_reaches_the_public_state_file(tmp_path):
    fake = FakeGitHub([
        make_repo(1, days_ago(40), stars=20, topics=('osint', 'people-search'), description='Пробив по номеру телефона: поиск по базам',
                  homepage='https://example.invalid/bot'),
        make_repo(2, days_ago(40), stars=20, owner='bystander'),
    ])
    llm = FakeLLM(reply=lambda user: {**VALID_REPLY, 'summary': 'UNIQUE-SUMMARY-TEXT'})

    run_llm(fake, tmp_path, llm)

    raw = (tmp_path / 'state.jsonl').read_text(encoding='utf-8')
    repos = state.load(str(tmp_path))
    assert repos[1]['class']['sensitivity'] == 'high'
    assert repos[1]['description'] is None and repos[1]['topics'] == [] and repos[1]['homepage'] is None
    assert repos[1]['class']['summary'] is None and repos[1]['review']['result']['summary'] == ''
    assert 'Пробив' not in raw and 'people-search' not in raw
    assert repos[2]['description'] and repos[2]['class']['summary'] == 'UNIQUE-SUMMARY-TEXT'


def test_redaction_can_be_switched_off(tmp_path):
    keep = copy.deepcopy(CONFIG)
    keep['privacy']['redact_high_sensitivity'] = False
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, description='Пробив по номеру телефона: поиск по базам')])

    run_llm(fake, tmp_path, None, config=keep)

    assert state.load(str(tmp_path))[1]['description'].startswith('Пробив')


def test_a_stale_review_keeps_applying_until_it_is_replaced_so_classifications_do_not_flap(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    run_llm(fake, tmp_path, FakeLLM())
    fake.repos[1]['readme'] = '# Rewritten\n\nA completely different README than the one that was reviewed. ' * 5

    summary = run_llm(fake, tmp_path, None, now=NOW + timedelta(days=1))  # no client: the new README cannot be reviewed

    klass = state.load(str(tmp_path))[1]['class']
    assert summary['review_pending'] == 1
    assert klass['reviewed'] is True and klass['summary'] == VALID_REPLY['summary']


def test_repositories_that_never_passed_triage_are_redacted_too_when_the_rules_call_them_high(tmp_path):
    # one star at 40 days old is below the age-scaled floor, so these are never enriched or classified
    fake = FakeGitHub([
        make_repo(1, days_ago(40), stars=1, topics=('osint', 'people-search'), description='Пробив по номеру телефона: поиск по базам',
                  homepage='https://t.me/some_lookup_bot'),
        make_repo(2, days_ago(40), stars=1, owner='bystander'),
    ])

    run_llm(fake, tmp_path, None)

    raw = (tmp_path / 'state.jsonl').read_text(encoding='utf-8')
    repos = state.load(str(tmp_path))
    assert repos[1]['tier1'] is False and 'class' not in repos[1]
    assert repos[1]['description'] is None and repos[1]['topics'] == [] and repos[1]['homepage'] is None
    assert 'Пробив' not in raw and 't.me/some_lookup_bot' not in raw
    assert repos[2]['description']  # ordinary repositories keep their text


def test_redaction_extends_to_every_candidate_of_an_owner_with_a_high_sensitivity_repo(tmp_path):
    fake = FakeGitHub([
        make_repo(1, days_ago(40), stars=1, owner='botfarm', description='Пробив по номеру телефона: поиск по базам'),
        make_repo(2, days_ago(40), stars=1, owner='BotFarm', description='Generic wording the rules do not recognise', homepage='https://t.me/x'),
        make_repo(3, days_ago(40), stars=1, owner='bystander', description='Generic wording the rules do not recognise'),
    ])

    run_llm(fake, tmp_path, None)

    repos = state.load(str(tmp_path))
    assert repos[1]['description'] is None and repos[2]['description'] is None and repos[2]['homepage'] is None
    assert repos[3]['description'] == 'Generic wording the rules do not recognise'


def test_publishable_repos_of_an_owner_with_a_high_sensitivity_repo_keep_their_text_and_their_review(tmp_path):
    fake = FakeGitHub([
        make_repo(1, days_ago(40), stars=20, owner='mixed', description='Пробив по номеру телефона: поиск по базам'),
        make_repo(2, days_ago(40), stars=20, owner='mixed'),             # a legitimate tool by the same owner
        make_repo(3, days_ago(40), stars=1, owner='mixed', description='Generic wording the rules do not recognise'),
    ])
    llm = FakeLLM(reply=lambda user: {**VALID_REPLY, 'summary': 'KEEP-THIS-SUMMARY'})

    run_llm(fake, tmp_path, llm)

    repos = state.load(str(tmp_path))
    assert repos[1]['class']['sensitivity'] == 'high' and repos[1]['description'] is None
    assert repos[1]['review']['result']['summary'] == '' and repos[1]['review']['result']['redacted'] is True
    assert repos[2]['description'] and repos[2]['class']['summary'] == 'KEEP-THIS-SUMMARY'
    assert repos[2]['review']['result']['summary'] == 'KEEP-THIS-SUMMARY' and 'redacted' not in repos[2]['review']['result']
    assert repos[3]['description'] is None  # never classified, so it stays redacted


def test_redacted_repositories_are_not_reviewed_again_every_run(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20, description='Пробив по номеру телефона: поиск по базам')])
    llm = FakeLLM()
    run_llm(fake, tmp_path, llm)

    summary = run_llm(fake, tmp_path, llm, now=NOW + timedelta(days=1))

    assert len(llm.calls) == 1 and summary['llm_calls'] == 0 and summary['review_cached'] == 1


def test_a_review_whose_text_was_wiped_by_accident_is_restored_on_the_next_run(tmp_path):
    fake = FakeGitHub([make_repo(1, days_ago(40), stars=20)])
    llm = FakeLLM()
    run_llm(fake, tmp_path, llm)
    repos = state.load(str(tmp_path))
    repos[1]['review']['result'].update({'summary': '', 'strengths': [], 'risks': []})   # the old redaction bug
    state.save(str(tmp_path), repos, NOW)

    summary = run_llm(fake, tmp_path, llm, now=NOW + timedelta(days=1))

    healed = state.load(str(tmp_path))[1]
    assert summary['llm_calls'] == 1 and healed['review']['result']['summary'] == VALID_REPLY['summary']
    assert healed['class']['summary'] == VALID_REPLY['summary']
