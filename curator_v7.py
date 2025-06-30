import requests
import base64
import os
import json
import time
from datetime import datetime
from typing import List, Dict, Optional
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('curator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class RepoInfo:
    """Data class for repository information"""
    name: str
    full_name: str
    html_url: str
    description: str
    stars: int
    language: str
    last_updated: str
    topics: List[str]
    license_name: Optional[str]

    @classmethod
    def from_github_api(cls, repo_data: Dict) -> 'RepoInfo':
        """Create RepoInfo from GitHub API response"""
        return cls(
            name=repo_data['name'],
            full_name=repo_data['full_name'],
            html_url=repo_data['html_url'],
            description=repo_data.get('description', 'No description'),
            stars=repo_data['stargazers_count'],
            language=repo_data.get('language', 'Unknown'),
            last_updated=repo_data['updated_at'][:10],  # YYYY-MM-DD format
            topics=repo_data.get('topics', []),
            license_name=repo_data.get('license', {}).get('name') if repo_data.get('license') else None
        )

class GitHubCurator:
    """Enhanced GitHub repository curator with improved functionality"""

    def __init__(self, config_path: str = 'config.json'):
        """Initialize curator with configuration"""
        self.config = self._load_config(config_path)
        self.api_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {self.config['github_token']}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.rate_limit_remaining = 5000  # GitHub default
        self.rate_limit_reset = time.time()

        # Validate token immediately
        self._validate_token()

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from file or environment"""
        default_config = {
            'github_token': os.getenv('GITHUB_TOKEN'),
            'username': os.getenv('GITHUB_USERNAME', 'hexacron'),
            'repo': os.getenv('GITHUB_REPO', 'ai-curator'),
            'file_path': 'README.md',
            'branch': 'main',
            'search_queries': [
                "OSINT AI stars:>10",
                "artificial intelligence security stars:>15",
                "machine learning cybersecurity stars:>10",
                "threat intelligence automation stars:>5",
                "security analysis tools AI stars:>8",
                "automated reconnaissance stars:>10",
                "AI penetration testing stars:>5",
                "cybersecurity machine learning stars:>12"
            ],
            'filters': {
                'min_stars': 5,
                'min_size': 500,
                'languages': ['Python', 'JavaScript', 'Go', 'Rust'],
                'exclude_keywords': ['awesome-list', 'tutorial-only']
            },
            'output_format': 'markdown',
            'max_repos_per_query': 50,
            'enable_caching': True,
            'cache_duration_hours': 24
        }

        # Try to load from config file
        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                try:
                    file_config = json.load(f)
                    default_config.update(file_config)
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode {config_path}. Using defaults.")


        # Validate required fields
        if not default_config['github_token']:
            raise ValueError("GitHub token not found. Set GITHUB_TOKEN environment variable or add to config.json")

        return default_config

    def _validate_token(self):
        """Validate GitHub token before proceeding"""
        try:
            response = requests.get(f"{self.api_url}/user", headers=self.headers)
            if response.status_code == 200:
                user_data = response.json()
                logger.info(f"✅ Authenticated as: {user_data.get('login')}")
                logger.info(f"Rate limit: {response.headers.get('X-RateLimit-Remaining')}/{response.headers.get('X-RateLimit-Limit')}")
            elif response.status_code == 401:
                raise ValueError("Invalid GitHub token. Please check your token and permissions.")
            else:
                logger.warning(f"Token validation returned: {response.status_code}")
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Failed to validate GitHub token: {e}")

    def _check_rate_limit(self):
        """Check and handle GitHub API rate limiting"""
        if self.rate_limit_remaining < 10:
            sleep_time = max(0, self.rate_limit_reset - time.time())
            if sleep_time > 0:
                logger.warning(f"Rate limit low. Sleeping for {sleep_time:.0f} seconds")
                time.sleep(sleep_time)

    def _make_api_request(self, url: str, params: Dict = None) -> Optional[Dict]:
        """Make API request with rate limiting and error handling"""
        self._check_rate_limit()

        try:
            response = requests.get(url, headers=self.headers, params=params)

            # Update rate limit info
            self.rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
            self.rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', time.time()))

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 403:
                logger.error("Rate limit exceeded or access forbidden")
                # Don't raise exception, just return None to allow script to continue
                return None
            else:
                logger.error(f"API request failed with status {response.status_code}: {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None

    def search_repositories(self, query: str) -> List[RepoInfo]:
        """Search GitHub repositories with enhanced filtering"""
        repos = []
        page = 1
        max_pages = 5  # Limit to avoid excessive API calls

        while page <= max_pages:
            refined_query = self._build_search_query(query)

            url = f"{self.api_url}/search/repositories"
            params = {
                'q': refined_query,
                'sort': 'stars',
                'order': 'desc',
                'page': page,
                'per_page': 100
            }

            logger.info(f"Searching repositories: page {page}, query: {refined_query}")

            data = self._make_api_request(url, params)

            # *** FIX IS HERE ***
            # If the API request failed, data will be None. Skip this iteration.
            if not data:
                logger.error(f"Failed to fetch data for query: {refined_query}")
                break # Exit loop for this query if there's an issue

            items = data.get('items', [])

            if not items:
                break

            for item in items:
                repo_info = RepoInfo.from_github_api(item)
                if self._should_include_repo(repo_info):
                    repos.append(repo_info)

            if len(repos) >= self.config['max_repos_per_query']:
                break

            page += 1
            time.sleep(1)  # Be nice to the API

        return repos[:self.config['max_repos_per_query']]

    def _build_search_query(self, base_query: str) -> str:
        """Build enhanced search query with filters"""
        # If the base query already has filters, use it as-is
        if 'stars:>' in base_query:
            return base_query

        filters = self.config['filters']

        if filters.get('languages'):
            lang_filter = ' OR '.join([f"language:{lang}" for lang in filters['languages']])
            base_query += f" ({lang_filter})"

        if filters.get('min_stars'):
            base_query += f" stars:>{filters['min_stars']}"

        exclude_words = ['awesome-list', 'tutorial-only']
        for keyword in exclude_words:
            base_query += f" -\"{keyword}\""

        return base_query

    def _should_include_repo(self, repo: RepoInfo) -> bool:
        """Additional filtering logic for repositories"""
        generic_names = ['awesome', 'list', 'collection', 'resources']
        if any(name in repo.name.lower() for name in generic_names):
            return repo.stars > 1000

        if len(repo.description) < 20:
            return repo.stars > 100

        return True

    def analyze_repositories(self, repos: List[RepoInfo]) -> Dict:
        """Analyze repository collection for insights"""
        if not repos:
            return {}

        analysis = {
            'total_repos': len(repos),
            'total_stars': sum(repo.stars for repo in repos),
            'languages': {},
            'topics': {},
            'licenses': {},
            'recent_repos': [],
            'top_starred': sorted(repos, key=lambda r: r.stars, reverse=True)[:20]
        }

        lang_count = {}
        topic_count = {}
        license_count = {}

        for repo in repos:
            lang = repo.language
            lang_count[lang] = lang_count.get(lang, 0) + 1

            for topic in repo.topics:
                topic_count[topic] = topic_count.get(topic, 0) + 1

            if repo.license_name:
                lic = repo.license_name
                license_count[lic] = license_count.get(lic, 0) + 1

        analysis['languages'] = lang_count
        analysis['topics'] = topic_count
        analysis['licenses'] = license_count
        analysis['recent_repos'] = sorted(
            repos,
            key=lambda r: r.last_updated,
            reverse=True
        )[:10]

        return analysis

    def format_output(self, all_repos: List[RepoInfo], analysis: Dict) -> str:
        """Format repository data for output"""
        if self.config['output_format'] == 'json':
            return self._format_json(all_repos, analysis)
        else:
            return self._format_markdown(all_repos, analysis)

    def _format_markdown(self, all_repos: List[RepoInfo], analysis: Dict) -> str:
        """Format as enhanced markdown"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        content = f"""# AI & OSINT Repository Curator

*Last updated: {timestamp}*

## 📊 Collection Summary

- **Total Repositories**: {analysis.get('total_repos', 0)}
- **Total Stars**: {analysis.get('total_stars', 0):,}
- **Search Queries**: {len(self.config['search_queries'])}

### 🔥 Top Languages
"""

        for lang, count in sorted(analysis.get('languages', {}).items(),
                                 key=lambda x: x[1], reverse=True)[:5]:
            content += f"- **{lang}**: {count} repositories\n"

        content += "\n### 🏷️ Popular Topics\n"

        for topic, count in sorted(analysis.get('topics', {}).items(),
                                  key=lambda x: x[1], reverse=True)[:10]:
            content += f"- `{topic}` ({count})\n"

        content += "\n## ⭐ Top Starred Repositories\n\n"

        for i, repo in enumerate(analysis.get('top_starred', [])[:20], 1):
            content += f"### {i}. [{repo.name}]({repo.html_url})\n"
            content += f"**{repo.stars:,} ⭐** | **{repo.language}** | Updated: {repo.last_updated}\n\n"
            content += f"{repo.description}\n\n"

            if repo.topics:
                topics_str = " ".join([f"`{topic}`" for topic in repo.topics[:5]])
                content += f"**Topics**: {topics_str}\n\n"

            content += "---\n\n"

        content += "\n## 🆕 Recently Updated\n\n"

        for repo in analysis.get('recent_repos', [])[:10]:
            content += f"- [{repo.name}]({repo.html_url}) - {repo.stars:,} ⭐ (Updated: {repo.last_updated})\n"

        content += f"\n## 📈 Collection Stats\n\n"
        content += f"Generated by AI Repository Curator | [View Source](https://github.com/{self.config['username']}/{self.config['repo']})\n"

        return content

    def _format_json(self, all_repos: List[RepoInfo], analysis: Dict) -> str:
        """Format as JSON"""
        output = {
            'metadata': {
                'generated_at': datetime.now().isoformat(),
                'total_repos': len(all_repos),
                'search_queries': self.config['search_queries']
            },
            'analysis': analysis,
            'repositories': [asdict(repo) for repo in all_repos]
        }
        return json.dumps(output, indent=2)

    def save_cache(self, data: List[RepoInfo], filename: str = 'cache.json'):
        """Save repository data to cache"""
        if not self.config.get('enable_caching'):
            return

        cache_data = {
            'timestamp': time.time(),
            'repositories': [asdict(repo) for repo in data]
        }

        with open(filename, 'w') as f:
            json.dump(cache_data, f, indent=2)

        logger.info(f"Saved {len(data)} repositories to cache")

    def load_cache(self, filename: str = 'cache.json') -> Optional[List[RepoInfo]]:
        """Load repository data from cache if valid"""
        if not self.config.get('enable_caching') or not Path(filename).exists():
            return None

        try:
            with open(filename, 'r') as f:
                cache_data = json.load(f)

            cache_age = time.time() - cache_data['timestamp']
            max_age = self.config.get('cache_duration_hours', 24) * 3600

            if cache_age > max_age:
                logger.info("Cache expired")
                return None

            repos = [RepoInfo(**repo_data) for repo_data in cache_data['repositories']]
            logger.info(f"Loaded {len(repos)} repositories from cache")
            return repos

        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return None

    def update_github_file(self, content: str, message: str = None) -> bool:
        """Update file on GitHub with better error handling"""
        if not message:
            message = f"Update curator results - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        url = f"{self.api_url}/repos/{self.config['username']}/{self.config['repo']}/contents/{self.config['file_path']}"

        try:
            # Get current file SHA if it exists
            get_response = requests.get(url, headers=self.headers)

            data = {
                'message': message,
                'content': base64.b64encode(content.encode()).decode(),
                'branch': self.config['branch']
            }

            if get_response.status_code == 200:
                data['sha'] = get_response.json()['sha']

            # Update or create file
            put_response = requests.put(url, headers=self.headers, json=data)

            if put_response.status_code in [200, 201]:
                logger.info("Successfully updated GitHub file")
                return True
            else:
                logger.error(f"Failed to update GitHub file: {put_response.status_code} - {put_response.text}")
                return False

        except Exception as e:
            logger.error(f"Error updating GitHub file: {e}")
            return False

    def run(self):
        """Main execution method"""
        logger.info("Starting AI Repository Curator")

        all_repos = self.load_cache()

        if not all_repos:
            all_repos = []

            for query in self.config['search_queries']:
                logger.info(f"Searching for: {query}")
                repos = self.search_repositories(query)
                all_repos.extend(repos)
                time.sleep(2)

            seen_urls = set()
            unique_repos = []
            for repo in all_repos:
                if repo.html_url not in seen_urls:
                    seen_urls.add(repo.html_url)
                    unique_repos.append(repo)

            all_repos = unique_repos
            logger.info(f"Found {len(all_repos)} unique repositories")

            self.save_cache(all_repos)

        analysis = self.analyze_repositories(all_repos)
        formatted_content = self.format_output(all_repos, analysis)
        success = self.update_github_file(formatted_content)

        if success:
            logger.info("Curator run completed successfully")
        else:
            logger.error("Curator run completed with errors")

        return success

def main():
    """Main function"""
    try:
        curator = GitHubCurator()
        curator.run()
    except Exception as e:
        logger.error(f"Curator failed: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
