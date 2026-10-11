from datetime import timedelta

from osint_tracker.discover import MAX_RESULTS, discover
from tests.helpers import NOW, FakeGitHub, days_ago, make_repo


def run(fake, topics=('osint',), start=None, end=NOW):
    return discover(fake, list(topics), start or days_ago(90), end, 0, sleep=lambda s: None)


def test_every_repo_is_found_when_a_topic_exceeds_the_search_cap():
    # 1,200 repos created one second apart: any off-by-one at a bisection boundary loses or duplicates one.
    start = NOW - timedelta(seconds=1199)
    fake = FakeGitHub(make_repo(i, start + timedelta(seconds=i)) for i in range(1200))

    found = run(fake, start=start)

    assert sorted(found) == list(range(1200))


def test_pileup_in_a_single_second_keeps_the_most_starred_and_terminates():
    created = days_ago(5).replace(microsecond=0)
    fake = FakeGitHub(make_repo(i, created, stars=i) for i in range(MAX_RESULTS + 100))

    found = run(fake)

    assert len(found) == MAX_RESULTS
    assert min(found) == 100  # ids double as star counts here: the 100 least-starred were cut


def test_a_repo_matching_several_topics_is_returned_once():
    fake = FakeGitHub([make_repo(1, days_ago(3), topics=('osint', 'geoint'))])

    found = run(fake, topics=('osint', 'geoint'))

    assert list(found) == [1]


def test_forks_archived_repos_and_repos_older_than_the_window_are_excluded():
    fake = FakeGitHub([
        make_repo(1, days_ago(10)),
        make_repo(2, days_ago(10), fork=True),
        make_repo(3, days_ago(10), archived=True),
        make_repo(4, days_ago(91)),
    ])

    assert list(run(fake)) == [1]
