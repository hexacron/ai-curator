"""Batched GraphQL enrichment of tracked repositories, plus the one REST lookup GraphQL cannot do cheaply."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from github_client import GitHubAPIError, GitHubClient
from osint_tracker import readme as readme_facts
from osint_tracker.clock import format_ts

logger = logging.getLogger(__name__)

BATCH_SIZE = 10  # README text makes responses large; 10 repositories cost 1 GraphQL point

# GitHub renders a README from these locations; other spellings are treated as "no README".
README_PATHS = ['README.md', 'readme.md', 'Readme.md', 'README.rst', 'README.txt', 'README',
                '.github/README.md', 'docs/README.md']

_README_FIELDS = '\n'.join(
    f'      readme{i}: object(expression: "HEAD:{path}") {{ ... on Blob {{ byteSize text }} }}'
    for i, path in enumerate(README_PATHS))

QUERY = """
query($ids: [ID!]!, $since: GitTimestamp!) {
  nodes(ids: $ids) {
    ... on Repository {
      nameWithOwner
      url
      description
      pushedAt
      isArchived
      stargazerCount
      forkCount
      watchers { totalCount }
      owner {
        login
        __typename
        ... on User { createdAt followers { totalCount } repositories(privacy: PUBLIC) { totalCount } }
        ... on Organization { createdAt repositories(privacy: PUBLIC) { totalCount } }
      }
      issues { totalCount }
      closedIssues: issues(states: CLOSED) { totalCount }
      pullRequests { totalCount }
      mergedPullRequests: pullRequests(states: MERGED) { totalCount }
      releases { totalCount }
      latestRelease { publishedAt }
__README__
      root: object(expression: "HEAD:") { ... on Tree { entries { name } } }
      workflows: object(expression: "HEAD:.github/workflows") { ... on Tree { entries { name } } }
      defaultBranchRef { target { ... on Commit {
        total: history { totalCount }
        recent: history(first: 100, since: $since) { totalCount nodes { author { name user { login } } } }
        latest: history(first: 1) { nodes { committedDate } }
      } } }
    }
  }
}
""".replace('__README__', _README_FIELDS)

_TEST_DIRS = {'tests', 'test', 'spec', 'specs', '__tests__', 'testing'}
_TEST_FILES = {'pytest.ini', 'tox.ini', 'conftest.py', 'jest.config.js', 'jest.config.ts', 'vitest.config.ts', 'vitest.config.js'}
_CI_ROOT = {'.gitlab-ci.yml', '.travis.yml', 'azure-pipelines.yml', 'jenkinsfile', '.circleci', 'bitbucket-pipelines.yml'}
_PACKAGE_FILES = {'pyproject.toml', 'setup.py', 'setup.cfg', 'package.json', 'go.mod', 'cargo.toml', 'pom.xml',
                  'build.gradle', 'gemfile', 'composer.json'}
_DOCKER_FILES = {'dockerfile', 'containerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'}


def fetch(client: GitHubClient, node_ids: List[str], now: datetime) -> Dict[str, Optional[dict]]:
    """Current facts per GraphQL node id, or None where the repository was deleted or went private."""
    since = format_ts(now - timedelta(days=30))
    result: Dict[str, Optional[dict]] = {}
    for offset in range(0, len(node_ids), BATCH_SIZE):
        chunk = node_ids[offset:offset + BATCH_SIZE]
        data = client.graphql(QUERY, {'ids': chunk, 'since': since})
        for node_id, node in zip(chunk, data['nodes']):
            result[node_id] = _parse(node) if node else None
    return result


def _entries(tree: Optional[dict]) -> List[str]:
    return [entry['name'] for entry in (tree or {}).get('entries') or []]


def _repo_files(root: List[str], workflows: List[str]) -> Dict[str, bool]:
    lowered = {name.lower() for name in root}
    return {
        'tests': bool(lowered & _TEST_DIRS or lowered & _TEST_FILES),
        'ci': bool(workflows or lowered & _CI_ROOT),
        'package': bool(lowered & _PACKAGE_FILES or any(name.endswith('.csproj') for name in lowered)),
        'docker': bool(lowered & _DOCKER_FILES),
    }


def _parse(node: dict) -> dict:
    """Flatten one GraphQL repository node. `readme_text` is returned for in-memory use only."""
    owner = node.get('owner') or {}
    readme_index = next((i for i in range(len(README_PATHS)) if node.get(f'readme{i}')), None)
    readme = node[f'readme{readme_index}'] if readme_index is not None else None

    commit = ((node.get('defaultBranchRef') or {}).get('target')) or {}
    recent = commit.get('recent') or {}
    authors = {(c['author'].get('user') or {}).get('login') or c['author'].get('name')
               for c in recent.get('nodes') or [] if c.get('author')}
    latest = (commit.get('latest') or {}).get('nodes') or []
    readme_summary = readme_facts.analyze((readme or {}).get('text'), (readme or {}).get('byteSize') or 0)
    readme_summary['path'] = README_PATHS[readme_index] if readme_index is not None else None

    return {
        'name': node['nameWithOwner'],
        'url': node['url'],
        'description': node.get('description'),
        'pushed_at': node.get('pushedAt'),
        'archived': bool(node.get('isArchived')),
        'stars': node['stargazerCount'],
        'forks': node['forkCount'],
        'readme_text': (readme or {}).get('text'),
        'facts': {
            'watchers': (node.get('watchers') or {}).get('totalCount', 0),
            'issues': (node.get('issues') or {}).get('totalCount', 0),
            'issues_closed': (node.get('closedIssues') or {}).get('totalCount', 0),
            'prs': (node.get('pullRequests') or {}).get('totalCount', 0),
            'prs_merged': (node.get('mergedPullRequests') or {}).get('totalCount', 0),
            'releases': (node.get('releases') or {}).get('totalCount', 0),
            'latest_release': (node.get('latestRelease') or {}).get('publishedAt'),
            'commits_total': (commit.get('total') or {}).get('totalCount', 0),
            'commits_30d': recent.get('totalCount', 0),
            'authors_30d': len(authors - {None}),
            'last_commit': latest[0]['committedDate'] if latest else None,
            'owner_created': owner.get('createdAt'),
            'owner_repos': (owner.get('repositories') or {}).get('totalCount'),
            'owner_followers': (owner.get('followers') or {}).get('totalCount'),
            'readme': readme_summary,
            'files': _repo_files(_entries(node.get('root')), _entries(node.get('workflows'))),
        },
    }


def earliest_commit(client: GitHubClient, full_name: str, total_commits: int) -> Optional[str]:
    """Committer date of a repository's first commit, or None if unknown. Commits are listed newest first,
    so with one commit per page the last page is the oldest. A failure here only delays the check to the
    next run; it must not cost the day's snapshot."""
    if not total_commits:
        return None
    try:
        page = client.get_json(f"{client.api_url}/repos/{full_name}/commits", {'per_page': 1, 'page': total_commits})
    except GitHubAPIError as e:
        logger.warning(f"Could not look up the first commit of {full_name}: {e}")
        return None
    return page[0]['commit']['committer']['date'] if page else None
