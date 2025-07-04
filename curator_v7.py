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
    description: Optional[str]
    stars: int
    language: str
    last_updated: str
    topics: List[str]
    license_name: Optional[str]

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
        
        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                try:
                    file_config = json.load(f)
                    default_config.update(file_config)
                except json.JSONDecodeError:
                    logger.warning(f"Could not decode {config_path}. Using defaults.")
        
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
    
    def _make_api_request(self, url: str, params: Dict = None) -> Optional[Dict]:
        """Make API request with rate limiting and error handling"""
        try:
            response = requests.get(url, headers=self.headers, params=params)
            
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
    
    def search_repositories(self, query: str) -> List[RepoInfo]:
        """Search GitHub repositories with enhanced filtering"""
        repos = []
        page = 1
        max_pages = 5
        
        while page <= max_pages:
            params = {
                'q': query,
                'sort': 'stars',
                'order': 'desc',
                'page': page,
                'per_page': 100
            }
            logger.info(f"Searching repositories: page {page}, query: {query}")
            
            data = self._make_api_request(f"{self.api_url}/search/repositories", params)
            
            if not data or not data.get('items'):
                break
                
            for item in data['items']:
                repo_info = RepoInfo.from_github_api(item)
                if self._should_include_repo(repo_info):
                    repos.append(repo_info)
            
            if len(repos) >= self.config['max_repos_per_query']:
                break
                
            page += 1
            time.sleep(1)
            
        return repos[:self.config['max_repos_per_query']]
    
    def _should_include_repo(self, repo: RepoInfo) -> bool:
        """Additional filtering logic for repositories, now handles None descriptions."""
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
            for query in queries:
                all_repos.extend(self.search_repositories(query))
                time.sleep(2)
            
            seen_urls = set()
            unique_repos = [repo for repo in all_repos if not (repo.html_url in seen_urls or seen_urls.add(repo.html_url))]
            
            logger.info(f"Found {len(unique_repos)} unique repositories")
            self.save_cache(unique_repos)
            all_repos = unique_repos
        
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
