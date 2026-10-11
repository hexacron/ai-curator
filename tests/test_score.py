from datetime import timedelta

import pytest

from osint_tracker import score
from osint_tracker.config import load_config
from tests.helpers import NOW, days_ago, ts

CONFIG = load_config('does-not-exist.json')
ALL_CHECKS = {'install': True, 'usage': True, 'screenshot': True, 'legal_notice': True, 'api_keys': False}


def facts(**overrides):
    base = {
        'watchers': 1, 'issues': 0, 'issues_closed': 0, 'prs': 0, 'prs_merged': 0, 'releases': 0, 'latest_release': None,
        'commits_total': 20, 'commits_30d': 20, 'authors_30d': 1, 'last_commit': ts(NOW - timedelta(days=1)),
        'owner_created': '2020-01-01T00:00:00Z', 'owner_repos': 10, 'owner_followers': 5,
        'readme': {'bytes': 3000, 'sha256': 'x', 'checks': dict(ALL_CHECKS)},
        'files': {'tests': True, 'ci': True, 'package': True, 'docker': False},
    }
    base.update(overrides)
    return base


def record(repo_id=1, age=20, stars=(10,), forks=(1,), scope='core', **overrides):
    rec = {'id': repo_id, 'full_name': f'owner{repo_id}/tool', 'owner': f'owner{repo_id}', 'tier1': True, 'gone': False,
           'created_at': ts(days_ago(age)), 'license': 'MIT', 'batch_publisher': False, 'earliest_commit': None,
           'stars': list(stars), 'forks': list(forks), 'enrich': facts(), 'class': {'scope': scope}}
    rec.update(overrides)
    return rec


# --- growth -----------------------------------------------------------------------------------------

def test_a_single_snapshot_falls_back_to_the_smoothed_lifetime_average():
    assert score.growth_rate([30], age=16) == pytest.approx(30 / (16 + 14))


def test_a_full_week_of_snapshots_uses_recent_growth_only():
    assert score.growth_rate([10, 20, 30, 40, 50, 60, 70, 80], age=300) == pytest.approx(10.0)


def test_a_partial_week_blends_recent_growth_with_the_lifetime_average():
    expected = (3 / 7) * 10 + (4 / 7) * (30 / (5 + 14))

    assert score.growth_rate([0, 10, 20, 30], age=5) == pytest.approx(expected)


def test_lost_stars_never_make_growth_negative_and_gaps_are_ignored():
    assert score.growth_rate([100, 90, 80, 70, 60, 50, 40, 30], age=100) >= 0
    assert score.growth_rate([5, None, None, 20], age=10) == pytest.approx((3 / 7) * 5 + (4 / 7) * (20 / 24))


def test_percentile_ranks_share_ties():
    assert score.percentile_ranks([0, 0, 5]) == pytest.approx([1 / 3, 1 / 3, 2.5 / 3])


# --- emerging ---------------------------------------------------------------------------------------

def test_emerging_requires_creation_and_first_commit_inside_the_window():
    inside = ts(days_ago(89))
    outside = ts(days_ago(91))

    assert score.is_emerging(record(age=89), NOW, 90)
    assert not score.is_emerging(record(age=91), NOW, 90)
    assert not score.is_emerging(record(age=30, earliest_commit=outside), NOW, 90)  # imported history
    assert score.is_emerging(record(age=30, earliest_commit=inside), NOW, 90)
    assert score.is_emerging(record(age=30, earliest_commit=None), NOW, 90)  # not looked up yet


# --- integrity --------------------------------------------------------------------------------------

def test_a_clean_repository_has_full_integrity():
    assert score.integrity(record(), facts(), stars=40, forks=5) == (1.0, [])


def test_a_mass_publisher_with_an_unclosed_issue_pile_is_unverified():
    rec = record(batch_publisher=True)
    mult, flags = score.integrity(rec, facts(issues=21, issues_closed=0), stars=5, forks=0)

    assert set(flags) == {'batch_publisher', 'unclosed_issue_pile'}
    assert mult < CONFIG['scoring']['unverified_below']


def test_a_brand_new_owner_with_outsized_watchers_is_flagged_but_still_verified():
    rec = record(age=18)
    new_owner = ts(days_ago(18))
    mult, flags = score.integrity(rec, facts(owner_created=new_owner, watchers=25), stars=228, forks=12)

    assert set(flags) == {'new_owner_account', 'watchers_outlier'}
    assert mult >= CONFIG['scoring']['unverified_below']


def test_stars_without_forks_needs_a_meaningful_star_count():
    assert 'stars_without_forks' not in score.integrity(record(), facts(), stars=40, forks=0)[1]
    assert 'stars_without_forks' in score.integrity(record(), facts(), stars=400, forks=2)[1]


def test_penalties_stack_but_never_drop_below_the_floor():
    rec = record(batch_publisher=True, clone_of=9)
    mult, _ = score.integrity(rec, facts(issues=40, issues_closed=0, watchers=30, owner_created=rec['created_at']), 300, 0)

    assert mult == score.MIN_INTEGRITY


# --- components -------------------------------------------------------------------------------------

def test_activity_rises_with_commits_and_recency():
    quiet = score.activity(facts(commits_30d=0, last_commit=ts(days_ago(60))), NOW)
    steady = score.activity(facts(commits_30d=8), NOW)
    busy = score.activity(facts(commits_30d=80, latest_release=ts(days_ago(3)), releases=4), NOW)

    assert quiet < steady < busy <= 1.0


def test_maturity_is_full_for_a_complete_repository_and_loses_exactly_the_license_weight_without_one():
    full = score.maturity(record(), facts())

    assert full == pytest.approx(1.0)
    assert full - score.maturity(record(license=None), facts()) == pytest.approx(0.20)


def test_community_rewards_closed_issues_prs_and_a_healthy_fork_ratio():
    lonely = score.community(facts(), stars=100, forks=0)
    engaged = score.community(facts(issues=10, issues_closed=9, prs=20, authors_30d=5), stars=100, forks=20)

    assert lonely < 0.3 < engaged


def test_confidence_grows_with_snapshots_and_age():
    young_once = score.confidence(record(age=3, stars=(5,)), NOW)
    old_once = score.confidence(record(age=40, stars=(5,)), NOW)
    old_week = score.confidence(record(age=40, stars=(1, 2, 3, 4, 5, 6, 7)), NOW)

    assert young_once < old_once < old_week <= 1.0
    assert young_once < 0.5 <= old_once  # the default Rising gate


# --- totals -----------------------------------------------------------------------------------------

def test_missing_utility_renormalises_the_remaining_weights():
    components = {'momentum': 1.0, 'activity': 0.5, 'maturity': 0.0, 'community': 0.0, 'utility': None}
    weights = CONFIG['scoring']['profiles']['rising']

    assert score.total(components, weights, 1.0, 1.0) == pytest.approx(100 * (0.45 + 0.1) / 0.9, abs=0.1)


def test_relevance_and_integrity_scale_the_total():
    components = {'momentum': 1.0, 'activity': 1.0, 'maturity': 1.0, 'community': 1.0, 'utility': None}
    weights = CONFIG['scoring']['profiles']['overall']

    assert score.total(components, weights, 1.0, 1.0) == 100.0
    assert score.total(components, weights, 0.6, 0.5) == 30.0


def test_score_all_ranks_momentum_within_cohorts_and_skips_unscorable_records():
    emerging_low = record(1, age=10, stars=(5,))
    emerging_high = record(2, age=10, stars=(500,))
    established = record(3, age=200, stars=(10000,))
    out_of_scope = record(4, scope='out')
    not_enriched = record(5, enrich=None)

    scored = score.score_all([emerging_low, emerging_high, established, out_of_scope, not_enriched], NOW, CONFIG)

    assert scored == 3
    assert [r['score']['status'] for r in (emerging_low, emerging_high, established)] == ['emerging', 'emerging', 'established']
    # the huge established repo does not compress the two emerging ones
    assert emerging_low['score']['components']['momentum'] == pytest.approx(0.25)
    assert emerging_high['score']['components']['momentum'] == pytest.approx(0.75)
    assert established['score']['components']['momentum'] == pytest.approx(0.5)
    assert 'score' not in out_of_scope and 'score' not in not_enriched


def test_low_confidence_lowers_the_rank_key_but_not_the_score():
    fresh = record(1, age=3, stars=(5,))
    mature = record(2, age=3, stars=(5,))
    mature['stars'] = [1, 2, 3, 4, 5, 5, 5]

    score.score_all([fresh, mature], NOW, CONFIG)

    assert fresh['score']['confidence'] < mature['score']['confidence']
    gap = fresh['score']['totals']['rising'] - fresh['score']['rank_keys']['rising']
    assert gap == pytest.approx(CONFIG['scoring']['confidence_penalty'] * (1 - fresh['score']['confidence']), abs=0.1)


def test_a_verified_utility_rating_enters_the_score_and_a_missing_one_is_left_out():
    rated = record(1, age=20, stars=(10,), **{'class': {'scope': 'core', 'utility': 5}})
    unrated = record(2, age=20, stars=(10,), **{'class': {'scope': 'core', 'utility': None}})

    score.score_all([rated, unrated], NOW, CONFIG)

    assert rated['score']['components']['utility'] == 1.0 and unrated['score']['components']['utility'] is None
    assert rated['score']['totals']['rising'] > unrated['score']['totals']['rising']
