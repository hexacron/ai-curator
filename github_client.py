"""Shared GitHub API client: authenticated session, rate-limit-aware retries, REST and GraphQL."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
MAX_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 120

# GraphQL reports "node does not exist" as a per-node error next to otherwise valid data.
_TOLERATED_GRAPHQL_ERRORS = {"NOT_FOUND"}

# GitHub renders a README from these locations; other spellings are treated as "no README".
README_PATHS = ['README.md', 'readme.md', 'Readme.md', 'README.rst', 'README.txt', 'README',
                '.github/README.md', 'docs/README.md']

# GraphQL selection for a Repository node: one aliased `readmeN` blob per location in README_PATHS.
README_GRAPHQL_FIELDS = '\n'.join(
    f'      readme{i}: object(expression: "HEAD:{path}") {{ ... on Blob {{ byteSize text }} }}'
    for i, path in enumerate(README_PATHS))


def find_readme(node: dict):
    """(path, blob) of the first README location present on a GraphQL repository node, or (None, None).
    The blob is {'byteSize': int, 'text': str | None}."""
    for index, path in enumerate(README_PATHS):
        if node.get(f'readme{index}'):
            return path, node[f'readme{index}']
    return None, None


class GitHubAPIError(RuntimeError):
    """Raised when the GitHub API keeps failing; a partial result must never be published."""


def token_from_env() -> str:
    """The API token. It is read from the environment only, never from a config file."""
    token = os.getenv('GITHUB_TOKEN')
    if not token:
        raise ValueError("GitHub token not found. Set the GITHUB_TOKEN environment variable or add it to .env")
    return token


class GitHubClient:
    """Thin wrapper over a requests session. Every call retries rate limits, 5xx and network errors."""

    def __init__(self, token: str, api_url: str = API_URL):
        self.api_url = api_url
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        })

    def validate_token(self) -> None:
        """Validate the token before proceeding (the /rate_limit endpoint works for any token type)."""
        try:
            response = self.session.get(f"{self.api_url}/rate_limit", timeout=30)
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Failed to validate GitHub token: {e}")

        if response.status_code == 401:
            raise ValueError("Invalid GitHub token. Please check your token and permissions.")
        if response.status_code == 200:
            search_limit = response.json().get('resources', {}).get('search', {})
            logger.info(f"Token OK. Search rate limit: {search_limit.get('remaining')}/{search_limit.get('limit')}")
        else:
            logger.warning(f"Token validation returned: {response.status_code}")

    @staticmethod
    def retry_delay(response: requests.Response, attempt: int) -> Optional[float]:
        """Seconds to wait before retrying a failed response, or None if the failure is not retryable."""
        status = response.status_code
        backoff = 2.0 ** (attempt + 1)

        if status >= 500:
            return backoff

        if status in (403, 429):
            retry_after = response.headers.get('Retry-After')
            if retry_after and retry_after.isdigit():
                wait = float(retry_after)
            elif response.headers.get('X-RateLimit-Remaining') == '0':
                reset = int(response.headers.get('X-RateLimit-Reset', 0))
                wait = max(reset - time.time(), 0) + 1
            elif status == 429 or any(word in response.text.lower() for word in ('rate limit', 'abuse')):
                wait = backoff * 5  # secondary rate limit, no timing hint
            else:
                return None  # genuine permission error
            return wait if wait <= MAX_RATE_LIMIT_WAIT_SECONDS else None

        return None

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """Send a request and return its JSON body. Raises GitHubAPIError once retries are exhausted."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
            except requests.exceptions.RequestException as e:
                error = f"request failed: {e}"
                delay: Optional[float] = 2.0 ** (attempt + 1)
            else:
                if response.status_code == 200:
                    return response.json()
                error = f"status {response.status_code}: {response.text[:200]}"
                delay = self.retry_delay(response, attempt)

            if delay is None or attempt == MAX_RETRIES:
                raise GitHubAPIError(f"API request to {url} failed ({error})")
            logger.warning(f"API request failed ({error}); retrying in {delay:.0f}s "
                           f"({attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)

    def get_json(self, url: str, params: Optional[Dict] = None) -> Dict:
        """GET a JSON resource."""
        return self._request_json("GET", url, params=params)

    def graphql(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Run a GraphQL query and return its `data`. Per-node NOT_FOUND errors are tolerated; any other error raises."""
        body = self._request_json("POST", f"{self.api_url}/graphql", json={"query": query, "variables": variables or {}})
        errors = [e for e in body.get('errors') or [] if e.get('type') not in _TOLERATED_GRAPHQL_ERRORS]
        if errors or body.get('data') is None:
            raise GitHubAPIError(f"GraphQL query failed: {str(errors or body)[:300]}")
        return body['data']
