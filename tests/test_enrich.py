from github_client import GitHubAPIError
from osint_tracker import enrich
from tests.helpers import NOW, FakeGitHub, days_ago, gql_node, make_repo


def parsed(repo):
    """Run one repository through the real fetch/parse path."""
    fake = FakeGitHub([repo])
    return enrich.fetch(fake, [repo['node_id']], NOW)[repo['node_id']]


def test_a_deleted_repository_comes_back_as_none():
    repo = make_repo(1, days_ago(5), deleted=True)

    assert enrich.fetch(FakeGitHub([repo]), [repo['node_id']], NOW) == {repo['node_id']: None}


def test_readme_is_found_in_github_dir_and_its_location_recorded():
    repo = make_repo(1, days_ago(5))
    node = gql_node(repo)
    node['readme0'] = None
    node['readme6'] = {'byteSize': 2200, 'text': '## Installation\n' + 'x' * 2000}  # .github/README.md
    repo['gql'] = {k: node[k] for k in ('readme0', 'readme6')}

    facts = parsed(repo)['facts']['readme']

    assert facts['path'] == '.github/README.md'
    assert facts['bytes'] == 2200 and facts['checks']['install'] is True


def test_no_readme_is_recorded_as_empty_not_an_error():
    repo = make_repo(1, days_ago(5))
    repo['gql'] = {'readme0': None}

    result = parsed(repo)

    assert result['readme_text'] is None
    assert result['facts']['readme']['bytes'] == 0 and result['facts']['readme']['path'] is None


def test_authors_are_distinct_logins_with_a_name_fallback_for_unlinked_commits():
    repo = make_repo(1, days_ago(5))
    nodes = [{'author': {'name': 'A', 'user': {'login': 'alice'}}},
             {'author': {'name': 'Alice again', 'user': {'login': 'alice'}}},
             {'author': {'name': 'Bob (no GitHub account)', 'user': None}},
             {'author': None}]
    base = gql_node(repo)['defaultBranchRef']['target']
    repo['gql'] = {'defaultBranchRef': {'target': {**base, 'recent': {'totalCount': 4, 'nodes': nodes}}}}

    facts = parsed(repo)['facts']

    assert facts['commits_30d'] == 4 and facts['authors_30d'] == 2


def test_repo_files_are_read_from_the_root_and_workflow_listings():
    repo = make_repo(1, days_ago(5))
    repo['gql'] = {'root': {'entries': [{'name': 'Dockerfile'}, {'name': 'pyproject.toml'}, {'name': 'tests'}]},
                   'workflows': {'entries': [{'name': 'ci.yml'}]}}

    assert parsed(repo)['facts']['files'] == {'tests': True, 'ci': True, 'package': True, 'docker': True}

    repo['gql'] = {'root': {'entries': [{'name': 'main.py'}]}, 'workflows': None}
    assert parsed(repo)['facts']['files'] == {'tests': False, 'ci': False, 'package': False, 'docker': False}


def test_earliest_commit_is_the_last_page_of_one_commit_per_page():
    class Recorder(FakeGitHub):
        def get_json(self, url, params=None):
            self.params = params
            return super().get_json(url, params)

    repo = make_repo(1, days_ago(50), earliest_commit='2026-08-01T00:00:00Z')
    fake = Recorder([repo])

    assert enrich.earliest_commit(fake, repo['full_name'], 137) == '2026-08-01T00:00:00Z'
    assert fake.params == {'per_page': 1, 'page': 137}


def test_earliest_commit_failure_is_swallowed_and_empty_repos_cost_no_call():
    repo = make_repo(1, days_ago(5))
    fake = FakeGitHub([repo])

    assert enrich.earliest_commit(fake, repo['full_name'], 0) is None
    assert fake.calls == 0

    fake.fail_on_call = 1
    assert enrich.earliest_commit(fake, repo['full_name'], 5) is None
