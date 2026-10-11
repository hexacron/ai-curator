import pytest

from osint_tracker.triage import required_stars, triage
from tests.helpers import NOW, days_ago, make_repo

SETTINGS = {'floor_days_per_star': 15, 'batch_publisher_threshold': 3}


@pytest.mark.parametrize("age, expected", [(0, 1), (14.9, 1), (15.0, 1), (15.01, 2), (30.0, 2), (89.9, 6), (90.0, 6)])
def test_required_stars_grows_with_age(age, expected):
    assert required_stars(age, 15) == expected


def test_a_repo_passes_triage_exactly_at_the_floor():
    # 31 days old needs ceil(31/15) = 3 stars
    verdicts = triage([make_repo(1, days_ago(31), stars=2), make_repo(2, days_ago(31), stars=3)], NOW, SETTINGS)

    assert verdicts[1]['tier1'] is False
    assert verdicts[2]['tier1'] is True


def test_batch_publisher_is_flagged_at_the_threshold_regardless_of_owner_case():
    repos = [make_repo(1, days_ago(5), owner='Mallory'), make_repo(2, days_ago(5), owner='mallory'),
             make_repo(3, days_ago(5), owner='MALLORY'),
             make_repo(4, days_ago(5), owner='bob'), make_repo(5, days_ago(5), owner='bob')]

    verdicts = triage(repos, NOW, SETTINGS)

    assert [verdicts[i]['batch_publisher'] for i in (1, 2, 3, 4, 5)] == [True, True, True, False, False]
