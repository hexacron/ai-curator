"""In-memory GitHub stand-in that answers repository search, GraphQL enrichment lookups and first-commit lookups
like the real API: inclusive created ranges, stars-descending order, 100 per page, and a hard cap of 1000
results per search query."""
import re
from datetime import datetime, timedelta, timezone

from github_client import README_PATHS, GitHubAPIError

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
SEARCH_CAP = 1000

DEFAULT_README = ("# {name}\n\nOSINT geolocation tool for photos.\n\n## Installation\n\n```\npip install {name}\n```\n\n"
                  "## Usage\n\n```\n{name} photo.jpg\n```\n\n" + "More details about the tool. " * 100)


def ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_repo(repo_id: int, created: datetime, stars: int = 10, forks: int = 0, owner: str = 'alice',
              topics=('osint',), **overrides) -> dict:
    repo = {
        'id': repo_id,
        'node_id': f'R_{repo_id}',
        'name': f'repo{repo_id}',
        'full_name': f'{owner}/repo{repo_id}',
        'owner': {'login': owner, 'type': 'User'},
        'html_url': f'https://github.com/{owner}/repo{repo_id}',
        'description': 'OSINT geolocation tool for finding where a photo was taken',
        'language': 'Python',
        'topics': list(topics),
        'license': {'spdx_id': 'MIT', 'name': 'MIT License'},
        'homepage': '',
        'created_at': ts(created),
        'pushed_at': ts(created),
        'size': 100,
        'open_issues_count': 0,
        'archived': False,
        'fork': False,
        'stargazers_count': stars,
        'forks_count': forks,
        'deleted': False,
        'earliest_commit': None,  # defaults to the creation time
        'readme': None,           # defaults to DEFAULT_README
        'gql': {},                # overrides merged over the GraphQL node
    }
    repo.update(overrides)
    return repo


def gql_node(repo: dict) -> dict:
    text = repo['readme'] if repo['readme'] is not None else DEFAULT_README.format(name=repo['name'])
    node = {
        'nameWithOwner': repo['full_name'],
        'url': repo['html_url'],
        'description': repo['description'],
        'pushedAt': repo['pushed_at'],
        'isArchived': repo['archived'],
        'stargazerCount': repo['stargazers_count'],
        'forkCount': repo['forks_count'],
        'watchers': {'totalCount': 1},
        'owner': {'login': repo['owner']['login'], '__typename': 'User', 'createdAt': '2020-01-01T00:00:00Z',
                  'followers': {'totalCount': 3}, 'repositories': {'totalCount': 12}},
        'issues': {'totalCount': 0}, 'closedIssues': {'totalCount': 0},
        'pullRequests': {'totalCount': 0}, 'mergedPullRequests': {'totalCount': 0},
        'releases': {'totalCount': 0}, 'latestRelease': None,
        'root': {'entries': [{'name': 'README.md'}, {'name': 'tests'}]},
        'workflows': None,
        'defaultBranchRef': {'target': {
            'total': {'totalCount': 12},
            'recent': {'totalCount': 12, 'nodes': [{'author': {'name': 'Alice', 'user': {'login': 'alice'}}}]},
            'latest': {'nodes': [{'committedDate': repo['pushed_at']}]},
        }},
    }
    for index in range(len(README_PATHS)):
        node[f'readme{index}'] = {'byteSize': len(text), 'text': text} if index == 0 else None
    node.update(repo['gql'])
    return node


class FakeGitHub:
    api_url = "https://api.github.com"

    def __init__(self, repos=()):
        self.repos = {repo['id']: repo for repo in repos}
        self.calls = 0
        self.commit_lookups = 0
        self.fail_on_call = None  # raise GitHubAPIError on this (1-based) call number

    def add(self, repo: dict) -> None:
        self.repos[repo['id']] = repo

    def _tick(self) -> None:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise GitHubAPIError("simulated API failure")

    def get_json(self, url, params=None):
        self._tick()
        if url.endswith('/commits'):
            self.commit_lookups += 1
            full_name = url.split('/repos/')[1].rsplit('/commits', 1)[0]
            repo = next(r for r in self.repos.values() if r['full_name'] == full_name)
            return [{'commit': {'committer': {'date': repo['earliest_commit'] or repo['created_at']}}}]

        query, page, per_page = params['q'], params['page'], params['per_page']
        topic = re.search(r'topic:(\S+)', query).group(1)
        low, high = re.search(r'created:(\S+)\.\.(\S+)', query).groups()
        matches = sorted(
            (r for r in self.repos.values()
             if topic in r['topics'] and low <= r['created_at'] <= high and not r['fork'] and not r['archived']),
            key=lambda r: -r['stargazers_count'])
        offset = (page - 1) * per_page
        items = matches[offset:offset + per_page] if offset < SEARCH_CAP else []
        return {'total_count': len(matches), 'incomplete_results': False, 'items': items}

    def graphql(self, query, variables=None):
        self._tick()
        by_node = {r['node_id']: r for r in self.repos.values()}
        nodes = []
        for node_id in variables['ids']:
            repo = by_node.get(node_id)
            nodes.append(None if repo is None or repo['deleted'] else gql_node(repo))
        return {'nodes': nodes}


def days_ago(days: float, now: datetime = NOW) -> datetime:
    return now - timedelta(days=days)


# --- LLM stand-ins -----------------------------------------------------------------------------------

from llm_client import LLMResponse  # noqa: E402  (kept next to the fake that returns it)

VALID_REPLY = {
    'scope': 'core', 'scope_reason': 'Geolocates photos for investigators.', 'domain': 'geospatial-imagery',
    'domain_secondary': 'none', 'form': 'cli', 'ai_role': 'none', 'posture': 'passive', 'sensitivity': 'none',
    'sensitivity_tags': [], 'utility': 4, 'utility_evidence': ['OSINT geolocation tool for photos'],
    'summary': 'Finds where a photo was taken.', 'strengths': ['Clear usage'], 'risks': [],
    'language': 'en', 'confidence': 0.9,
}


class FakeLLM:
    """Stand-in for AnthropicClient. `reply` is a dict, or a callable(user_message) -> dict; `error` is raised
    instead when set; `errors` is an optional per-call sequence (None = succeed) consumed in order."""

    def __init__(self, reply=None, error=None, errors=None):
        self.reply = reply if reply is not None else dict(VALID_REPLY)
        self.error = error
        self.errors = list(errors or [])
        self.calls = []

    def create(self, model, system, user, schema, max_tokens, effort):
        self.calls.append({'model': model, 'system': system, 'user': user, 'schema': schema,
                           'max_tokens': max_tokens, 'effort': effort})
        failure = self.errors.pop(0) if self.errors else self.error
        if failure:
            raise failure
        data = self.reply(user) if callable(self.reply) else dict(self.reply)
        return LLMResponse(data, 1000, 200)
