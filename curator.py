import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

CACHE_FILE = 'cache.json'
MAX_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 120


class GitHubAPIError(RuntimeError):
    """Raised when the GitHub API keeps failing; a partial result must never be published."""


@dataclass
class RepoInfo:
    """Data class for repository information"""
    name: str
    full_name: str
    html_url: str
    description: Optional[str]
    stars: int
    language: str
    last_updated: str
    topics: List[str]
    license_name: Optional[str]
    is_fork: bool = False
    size_kb: int = 0

    @classmethod
    def from_github_api(cls, repo_data: Dict) -> 'RepoInfo':
        """Create RepoInfo from GitHub API response (keys may be present but null)"""
        license_info = repo_data.get('license') or {}
        return cls(
            name=repo_data.get('name') or 'N/A',
            full_name=repo_data.get('full_name') or 'N/A',
            html_url=repo_data.get('html_url') or '#',
            description=repo_data.get('description'),  # Can be None
            stars=repo_data.get('stargazers_count') or 0,
            language=repo_data.get('language') or 'Unknown',
            last_updated=(repo_data.get('pushed_at') or repo_data.get('updated_at') or '')[:10],
            topics=repo_data.get('topics') or [],
            license_name=license_info.get('name'),
            is_fork=bool(repo_data.get('fork')),
            size_kb=repo_data.get('size') or 0
        )


class GitHubCurator:
    """GitHub repository curator"""

    def __init__(self, config_path: str = 'config.json'):
        """Initialize curator with configuration"""
        self.config = self._load_config(config_path)
        self.api_url = "https://api.github.com"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        })

        self._validate_token()

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from file. The token is read from the environment only."""
        default_config = {
            'search_queries': [
                "topic:osint ai stars:>5",
                "topic:cybersecurity llm stars:>5",
                "topic:threat-intelligence ai stars:>5",
                "mcp security stars:>5",
                "mcp osint stars:>5",
                "agentic osint stars:>5",
                "agent security stars:>5",
                "llm security stars:>5",
                "prompt injection security stars:>5",
                "attack surface ai stars:>5",
                "reconnaissance ai stars:>5",
                "claude security agent stars:>5",
                "codex security stars:>5"
            ],
            'filters': {
                'min_stars': 5,
                'min_size': 500,
                'languages': ['Python', 'JavaScript', 'Go', 'Rust'],
                'exclude_keywords': ['awesome-list', 'tutorial-only']
            },
            'max_repos_per_query': 50,
            'enable_caching': True,
            'cache_duration_hours': 24,
            'advanced_options': {
                'include_forks': False,
                'max_age_days': 365,
                'require_license': False,
                'prefer_topics': [],
                'api_delay_seconds': 1.0
            }
        }

        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                try:
                    file_config = json.load(f)
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode {config_path}. Using defaults.")
                    file_config = {}
            if 'github_token' in file_config:
                logger.warning(
                    f"Ignoring 'github_token' in {config_path}: "
                    "set the GITHUB_TOKEN environment variable (or .env) instead."
                )
                del file_config['github_token']
            for key, value in file_config.items():
                if isinstance(default_config.get(key), dict) and isinstance(value, dict):
                    default_config[key].update(value)
                else:
                    default_config[key] = value

        self.token = os.getenv('GITHUB_TOKEN')
        if not self.token:
            raise ValueError("GitHub token not found. Set the GITHUB_TOKEN environment variable or add it to .env")

        search_queries = default_config['search_queries']
        default_config['search_queries'] = list(dict.fromkeys(query.strip() for query in search_queries if query.strip()))

        return default_config

    def _validate_token(self):
        """Validate GitHub token before proceeding (the /rate_limit endpoint works for any token type)"""
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
    def _retry_delay(response: requests.Response, attempt: int) -> Optional[float]:
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

    def _make_api_request(self, url: str, params: Dict = None) -> Dict:
        """GET a JSON resource, retrying on rate limits, 5xx and network errors. Raises GitHubAPIError on failure."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.get(url, params=params, timeout=30)
            except requests.exceptions.RequestException as e:
                error = f"request failed: {e}"
                delay = 2.0 ** (attempt + 1)
            else:
                if response.status_code == 200:
                    return response.json()
                error = f"status {response.status_code}: {response.text[:200]}"
                delay = self._retry_delay(response, attempt)

            if delay is None or attempt == MAX_RETRIES:
                raise GitHubAPIError(f"API request to {url} failed ({error})")
            logger.warning(f"API request failed ({error}); retrying in {delay:.0f}s "
                           f"({attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)

    def _cutoff_date(self) -> Optional[date]:
        """Oldest acceptable last-push date, or None when the recency filter is disabled."""
        max_age_days = int(self.config['advanced_options'].get('max_age_days', 0))
        if max_age_days <= 0:
            return None
        return datetime.now(timezone.utc).date() - timedelta(days=max_age_days)

    def _build_search_query(self, query: str) -> str:
        """Push supported filters into the GitHub search query to reduce wasted pages."""
        filters = self.config['filters']
        advanced = self.config['advanced_options']
        effective_query = query.strip()
        query_lower = effective_query.lower()

        min_stars = int(filters.get('min_stars', 0))
        if min_stars > 0 and 'stars:' not in query_lower:
            effective_query += f" stars:>={min_stars}"

        min_size_kb = int(filters.get('min_size', 0))
        if min_size_kb > 0 and 'size:' not in query_lower:
            effective_query += f" size:>={min_size_kb}"

        if not advanced.get('include_forks', False) and 'fork:' not in query_lower:
            effective_query += " fork:false"

        if 'archived:' not in query_lower:
            effective_query += " archived:false"

        cutoff = self._cutoff_date()
        if cutoff and 'pushed:' not in query_lower:
            effective_query += f" pushed:>={cutoff.isoformat()}"

        return effective_query

    def search_repositories(self, query: str) -> List[RepoInfo]:
        """Search GitHub repositories with enhanced filtering"""
        repos = []
        seen_urls = set()
        page = 1
        max_pages = 5
        api_delay = float(self.config['advanced_options'].get('api_delay_seconds', 1.0))
        effective_query = self._build_search_query(query)

        while page <= max_pages:
            params = {
                'q': effective_query,
                'sort': 'stars',
                'order': 'desc',
                'page': page,
                'per_page': 100
            }
            logger.info(f"Searching repositories: page {page}, query: {effective_query}")

            data = self._make_api_request(f"{self.api_url}/search/repositories", params)

            if not data.get('items'):
                break

            for item in data['items']:
                repo_info = RepoInfo.from_github_api(item)
                if repo_info.html_url in seen_urls:
                    continue
                if self._should_include_repo(repo_info):
                    seen_urls.add(repo_info.html_url)
                    repos.append(repo_info)

            if len(data['items']) < params['per_page']:
                break

            if len(repos) >= self.config['max_repos_per_query']:
                break

            page += 1
            time.sleep(api_delay)

        return repos[:self.config['max_repos_per_query']]

    def _is_recent_enough(self, last_updated: str) -> bool:
        """Check if repository was pushed within the configured recency window."""
        cutoff = self._cutoff_date()
        if cutoff is None:
            return True
        try:
            return date.fromisoformat(last_updated) >= cutoff
        except ValueError:
            return False

    def _preference_score(self, repo: RepoInfo) -> int:
        """Number of `prefer_topics` from configuration that the repository carries."""
        preferred_topics = self.config['advanced_options'].get('prefer_topics', [])
        if not preferred_topics:
            return 0
        repo_topics = {topic.lower() for topic in repo.topics}
        return sum(1 for topic in preferred_topics if topic.lower() in repo_topics)

    def _rank_key(self, repo: RepoInfo):
        """Sort key for output order: preferred topics first, then stars."""
        return (self._preference_score(repo), repo.stars)

    def _should_include_repo(self, repo: RepoInfo) -> bool:
        """Additional filtering logic for repositories, now handles None descriptions."""
        filters = self.config['filters']
        advanced = self.config['advanced_options']

        min_stars = int(filters.get('min_stars', 0))
        if repo.stars < min_stars:
            return False

        min_size_kb = int(filters.get('min_size', 0))
        if repo.size_kb < min_size_kb:
            return False

        languages = filters.get('languages', [])
        if languages and repo.language not in languages:
            return False

        if not advanced.get('include_forks', False) and repo.is_fork:
            return False

        if advanced.get('require_license', False) and not repo.license_name:
            return False

        if not self._is_recent_enough(repo.last_updated):
            return False

        exclude_keywords = filters.get('exclude_keywords', [])
        searchable_text = f"{repo.name} {repo.description or ''}".lower()
        if any(keyword.lower() in searchable_text for keyword in exclude_keywords):
            return False

        generic_names = ['awesome', 'list', 'collection', 'resources']
        if any(name in repo.name.lower() for name in generic_names):
            return repo.stars > 1000

        # Check for description existence before checking its length
        if not repo.description or len(repo.description) < 20:
            return repo.stars > 100

        return True

    def analyze_repositories(self, repos: List[RepoInfo]) -> Dict:
        """Analyze repository collection for insights"""
        if not repos:
            return {}

        analysis = {
            'total_repos': len(repos),
            'total_stars': sum(r.stars for r in repos),
            'languages': {},
            'topics': {},
            'top_ranked': sorted(repos, key=self._rank_key, reverse=True)[:20]
        }

        for repo in repos:
            analysis['languages'][repo.language] = analysis['languages'].get(repo.language, 0) + 1
            for topic in repo.topics:
                analysis['topics'][topic] = analysis['topics'].get(topic, 0) + 1

        return analysis

    def format_output(self, analysis: Dict) -> str:
        """Format repository data for output"""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        content = f"# AI & OSINT Repository Curator\n\n*Last updated: {timestamp}*\n\n"
        content += f"## 📊 Collection Summary\n\n- **Total Repositories**: {analysis.get('total_repos', 0)}\n"
        content += f"- **Total Stars**: {analysis.get('total_stars', 0):,}\n\n"

        content += "### 🔥 Top Languages\n"
        for lang, count in sorted(analysis.get('languages', {}).items(), key=lambda x: x[1], reverse=True)[:5]:
            content += f"- **{lang}**: {count} repositories\n"

        content += "\n### 🏷️ Popular Topics\n"
        for topic, count in sorted(analysis.get('topics', {}).items(), key=lambda x: x[1], reverse=True)[:10]:
            content += f"- `{topic}` ({count})\n"

        content += "\n## ⭐ Top Repositories\n\n"
        for i, repo in enumerate(analysis.get('top_ranked', []), 1):
            description = ' '.join((repo.description or 'No description provided.').split())
            content += f"### {i}. [{repo.name}]({repo.html_url})\n"
            content += f"**{repo.stars:,} ⭐** | **{repo.language}** | Updated: {repo.last_updated}\n\n"
            content += f"{description}\n\n"
            if repo.topics:
                content += f"**Topics**: {' '.join([f'`{t}`' for t in repo.topics[:5]])}\n\n"
            content += "---\n\n"

        return content

    def _config_fingerprint(self) -> str:
        """Hash of every setting that changes search results, so a config edit invalidates the cache."""
        relevant = {key: self.config[key] for key in ('search_queries', 'filters', 'advanced_options', 'max_repos_per_query')}
        return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()

    def save_cache(self, data: List[RepoInfo], filename: str = CACHE_FILE):
        """Write results. Always written: generate_website.py reads this file as its data source."""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': time.time(),
                'config_hash': self._config_fingerprint(),
                'repositories': [asdict(repo) for repo in data]
            }, f, indent=2)

        logger.info(f"Saved {len(data)} repositories to {filename}")

    def load_cache(self, filename: str = CACHE_FILE) -> Optional[List[RepoInfo]]:
        """Load repository data from cache if enabled, fresh and produced by the current config"""
        if not self.config.get('enable_caching') or not Path(filename).exists():
            return None

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if data.get('config_hash') != self._config_fingerprint():
                logger.info("Cache was produced by a different config; ignoring it")
                return None

            if time.time() - data['timestamp'] > self.config['cache_duration_hours'] * 3600:
                logger.info("Cache expired")
                return None

            repos = [RepoInfo(**repo_data) for repo_data in data['repositories']]
            logger.info(f"Loaded {len(repos)} repositories from cache")
            return repos

        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return None

    def run(self):
        """Main execution method. Raises (and writes nothing) if the results would be empty or incomplete."""
        logger.info("Starting AI Repository Curator")

        all_repos = self.load_cache()

        if not all_repos:
            all_repos = []
            api_delay = float(self.config['advanced_options'].get('api_delay_seconds', 1.0))
            seen_urls = set()
            for query in self.config['search_queries']:
                for repo in self.search_repositories(query):
                    if repo.html_url in seen_urls:
                        continue
                    seen_urls.add(repo.html_url)
                    all_repos.append(repo)
                time.sleep(api_delay)

            if not all_repos:
                raise GitHubAPIError("No repositories found; refusing to overwrite existing output")

            all_repos.sort(key=self._rank_key, reverse=True)

            logger.info(f"Found {len(all_repos)} unique repositories")
            self.save_cache(all_repos)

        analysis = self.analyze_repositories(all_repos)
        formatted_content = self.format_output(analysis)

        # Write to README.md locally for the action to pick up
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(formatted_content)

        logger.info("Curator run completed successfully")


def main():
    """Main function"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('curator.log'),
            logging.StreamHandler()
        ]
    )
    try:
        curator = GitHubCurator()
        curator.run()
    except Exception as e:
        logger.error(f"Curator failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
