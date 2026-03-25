import requests
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
        """Create RepoInfo from GitHub API response"""
        return cls(
            name=repo_data.get('name', 'N/A'),
            full_name=repo_data.get('full_name', 'N/A'),
            html_url=repo_data.get('html_url', '#'),
            description=repo_data.get('description'), # Can be None
            stars=repo_data.get('stargazers_count', 0),
            language=repo_data.get('language', 'Unknown'),
            last_updated=repo_data.get('updated_at', '')[:10],
            topics=repo_data.get('topics', []),
            license_name=repo_data.get('license', {}).get('name') if repo_data.get('license') else None,
            is_fork=repo_data.get('fork', False),
            size_kb=repo_data.get('size', 0)
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
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.rate_limit_remaining = 5000  # GitHub default
        self.rate_limit_reset = time.time()
        
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
            'output_format': 'markdown',
            'max_repos_per_query': 50,
            'enable_caching': True,
            'cache_duration_hours': 24
        }
        
        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                try:
                    file_config = json.load(f)
                    for key, value in file_config.items():
                        if (
                            key in default_config
                            and isinstance(default_config[key], dict)
                            and isinstance(value, dict)
                        ):
                            default_config[key].update(value)
                        else:
                            default_config[key] = value
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode {config_path}. Using defaults.")
        
        if not default_config['github_token']:
            raise ValueError("GitHub token not found. Set GITHUB_TOKEN environment variable or add to config.json")

        search_queries = default_config.get('search_queries', [])
        default_config['search_queries'] = list(dict.fromkeys(query.strip() for query in search_queries if query.strip()))
            
        return default_config
    
    def _validate_token(self):
        """Validate GitHub token before proceeding"""
        try:
            response = self.session.get(f"{self.api_url}/user", timeout=30)
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
    
    def _make_api_request(self, url: str, params: Dict = None) -> Optional[Dict]:
        """Make API request with rate limiting and error handling"""
        try:
            response = self.session.get(url, params=params, timeout=30)
            
            self.rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
            self.rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', time.time()))
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API request failed with status {response.status_code}: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None

    def _build_search_query(self, query: str) -> str:
        """Push supported filters into the GitHub search query to reduce wasted pages."""
        filters = self.config.get('filters', {})
        advanced = self.config.get('advanced_options', {})
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

        max_age_days = int(advanced.get('min_last_update_days', 365))
        if max_age_days > 0 and 'pushed:' not in query_lower:
            pushed_after = datetime.utcnow().date().fromordinal(
                datetime.utcnow().date().toordinal() - max_age_days
            )
            effective_query += f" pushed:>={pushed_after.isoformat()}"

        return effective_query
    
    def search_repositories(self, query: str) -> List[RepoInfo]:
        """Search GitHub repositories with enhanced filtering"""
        repos = []
        seen_urls = set()
        page = 1
        max_pages = 5
        api_delay = float(self.config.get('advanced_options', {}).get('api_delay_seconds', 1.0))
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
            
            if not data or not data.get('items'):
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
        """Check if repository was updated within configured recency window."""
        max_age_days = int(self.config.get('advanced_options', {}).get('min_last_update_days', 365))
        if not last_updated:
            return False
        try:
            updated_dt = datetime.strptime(last_updated, "%Y-%m-%d")
        except ValueError:
            return False
        age_days = (datetime.utcnow() - updated_dt).days
        return age_days <= max_age_days

    def _preference_score(self, repo: RepoInfo) -> int:
        """Compute score boost for preferred topics from configuration."""
        preferred_topics = self.config.get('advanced_options', {}).get('prefer_topics', [])
        if not preferred_topics:
            return 0
        repo_topics = {topic.lower() for topic in repo.topics}
        return sum(1 for topic in preferred_topics if topic.lower() in repo_topics)
    
    def _should_include_repo(self, repo: RepoInfo) -> bool:
        """Additional filtering logic for repositories, now handles None descriptions."""
        filters = self.config.get('filters', {})
        advanced = self.config.get('advanced_options', {})

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
            'licenses': {},
            'recent_repos': sorted(repos, key=lambda r: r.last_updated, reverse=True)[:10],
            'top_starred': sorted(repos, key=lambda r: r.stars, reverse=True)[:20]
        }
        
        for repo in repos:
            analysis['languages'][repo.language] = analysis['languages'].get(repo.language, 0) + 1
            for topic in repo.topics:
                analysis['topics'][topic] = analysis['topics'].get(topic, 0) + 1
            if repo.license_name:
                analysis['licenses'][repo.license_name] = analysis['licenses'].get(repo.license_name, 0) + 1
                
        return analysis

    def format_output(self, all_repos: List[RepoInfo], analysis: Dict) -> str:
        """Format repository data for output"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"# AI & OSINT Repository Curator\n\n*Last updated: {timestamp}*\n\n"
        content += f"## 📊 Collection Summary\n\n- **Total Repositories**: {analysis.get('total_repos', 0)}\n"
        content += f"- **Total Stars**: {analysis.get('total_stars', 0):,}\n\n"
        
        content += "### 🔥 Top Languages\n"
        for lang, count in sorted(analysis.get('languages', {}).items(), key=lambda x: x[1], reverse=True)[:5]:
            content += f"- **{lang}**: {count} repositories\n"
            
        content += "\n### 🏷️ Popular Topics\n"
        for topic, count in sorted(analysis.get('topics', {}).items(), key=lambda x: x[1], reverse=True)[:10]:
            content += f"- `{topic}` ({count})\n"
            
        content += "\n## ⭐ Top Starred Repositories\n\n"
        for i, repo in enumerate(analysis.get('top_starred', []), 1):
            content += f"### {i}. [{repo.name}]({repo.html_url})\n"
            content += f"**{repo.stars:,} ⭐** | **{repo.language}** | Updated: {repo.last_updated}\n\n"
            content += f"{repo.description or 'No description provided.'}\n\n"
            if repo.topics:
                content += f"**Topics**: {' '.join([f'`{t}`' for t in repo.topics[:5]])}\n\n"
            content += "---\n\n"
            
        return content

    def save_cache(self, data: List[RepoInfo], filename: str = 'cache.json'):
        """Save repository data to cache"""
        if not self.config.get('enable_caching'):
            return
            
        with open(filename, 'w') as f:
            json.dump({'timestamp': time.time(), 'repositories': [asdict(repo) for repo in data]}, f, indent=2)
        
        logger.info(f"Saved {len(data)} repositories to cache")
    
    def load_cache(self, filename: str = 'cache.json') -> Optional[List[RepoInfo]]:
        """Load repository data from cache if valid"""
        if not self.config.get('enable_caching') or not Path(filename).exists():
            return None
        
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
            
            if time.time() - data['timestamp'] > self.config.get('cache_duration_hours', 24) * 3600:
                logger.info("Cache expired")
                return None
            
            repos = [RepoInfo(**repo_data) for repo_data in data['repositories']]
            logger.info(f"Loaded {len(repos)} repositories from cache")
            return repos
            
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return None
    
    def run(self):
        """Main execution method"""
        logger.info("Starting AI Repository Curator")
        
        all_repos = self.load_cache()
        
        if not all_repos:
            all_repos = []
            queries = self.config.get('search_queries', [])
            api_delay = float(self.config.get('advanced_options', {}).get('api_delay_seconds', 1.0))
            seen_urls = set()
            for query in queries:
                for repo in self.search_repositories(query):
                    if repo.html_url in seen_urls:
                        continue
                    seen_urls.add(repo.html_url)
                    all_repos.append(repo)
                time.sleep(api_delay)

            all_repos.sort(
                key=lambda repo: (self._preference_score(repo), repo.stars),
                reverse=True
            )
            
            logger.info(f"Found {len(all_repos)} unique repositories")
            self.save_cache(all_repos)
        
        analysis = self.analyze_repositories(all_repos)
        formatted_content = self.format_output(all_repos, analysis)
        
        # Write to README.md locally for the action to pick up
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(formatted_content)
            
        logger.info("Curator run completed successfully")

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
