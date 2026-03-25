import json
import os
from datetime import datetime


def _normalize_repo(raw_repo):
    """Normalize repository schema from cache.json and repositories.json."""
    topics = raw_repo.get('topics', [])
    if not isinstance(topics, list):
        topics = []

    return {
        'name': raw_repo.get('name', 'Unknown'),
        'full_name': raw_repo.get('full_name', raw_repo.get('name', 'Unknown')),
        'html_url': raw_repo.get('html_url') or raw_repo.get('url') or '#',
        'description': raw_repo.get('description') or 'No description available.',
        'stars': int(raw_repo.get('stars', 0) or 0),
        'language': raw_repo.get('language') or 'N/A',
        'last_updated': raw_repo.get('last_updated') or raw_repo.get('updated') or 'N/A',
        'topics': topics,
    }


def _merge_repo_lists(cache_repos, fallback_repos):
    """Merge repository sources with URL-based deduplication."""
    merged = {}

    for source in (cache_repos, fallback_repos):
        for raw_repo in source:
            repo = _normalize_repo(raw_repo)
            key = repo['html_url'] if repo['html_url'] != '#' else repo['full_name']
            existing = merged.get(key)
            if existing is None:
                merged[key] = repo
                continue

            # Prefer entry with more stars, then with more topics.
            is_better = (
                repo['stars'] > existing['stars']
                or (
                    repo['stars'] == existing['stars']
                    and len(repo['topics']) > len(existing['topics'])
                )
            )
            if is_better:
                merged[key] = repo

    return sorted(merged.values(), key=lambda repo: repo.get('stars', 0), reverse=True)

def generate_website():
    """
    Generates a clean, self-contained HTML website from repository data,
    ensuring no external artifacts are included.
    """
    
    # --- Setup and Data Loading ---
    docs_dir = 'docs'
    if not os.path.exists(docs_dir):
        os.makedirs(docs_dir)

    cache_file = 'cache.json'
    cache_repos = []
    fallback_repos = []

    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cache_repos = data.get('repositories', [])
            print(f"✅ Found {len(cache_repos)} repositories in {cache_file}.")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ Could not read or parse {cache_file}: {e}")
            
    if os.path.exists('repositories.json'):
        try:
            with open('repositories.json', 'r', encoding='utf-8') as f:
                fallback_repos = json.load(f)
            print(f"✅ Found {len(fallback_repos)} repositories in repositories.json.")
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ Could not read or parse repositories.json: {e}")

    repos = _merge_repo_lists(cache_repos, fallback_repos)
    print(f"✅ Using {len(repos)} merged repositories after normalization and deduplication.")
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    # --- HTML Generation ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en" class="">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI & OSINT Repository Curator</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script>
            // Function to apply the theme
            function applyTheme(theme) {{
                if (theme === 'dark') {{
                    document.documentElement.classList.add('dark');
                }} else {{
                    document.documentElement.classList.remove('dark');
                }}
            }}

            // Function to toggle the theme
            function toggleTheme() {{
                const isDark = document.documentElement.classList.contains('dark');
                const newTheme = isDark ? 'light' : 'dark';
                localStorage.setItem('theme', newTheme);
                applyTheme(newTheme);
            }}

            // Apply theme on initial page load
            document.addEventListener('DOMContentLoaded', () => {{
                const savedTheme = localStorage.getItem('theme');
                const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                if (savedTheme) {{
                    applyTheme(savedTheme);
                }} else {{
                    applyTheme(prefersDark ? 'dark' : 'light');
                }}
            }});
        </script>
    </head>
    <body class="bg-gray-100 dark:bg-gray-900 font-sans transition-colors duration-300">
        <div class="container mx-auto px-4 py-8">
            <header class="text-center mb-10 relative">
                <h1 class="text-4xl md:text-5xl font-bold text-gray-800 dark:text-gray-100">AI & OSINT Repository Curator</h1>
                <p class="text-gray-600 dark:text-gray-400 mt-2">A curated list of top-tier projects in AI, OSINT, and Cybersecurity.</p>
                <p class="text-sm text-gray-400 dark:text-gray-500 mt-1">Last updated: {timestamp}</p>
                <button onclick="toggleTheme()" class="absolute top-0 right-0 p-2 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none" aria-label="Toggle theme">
                    <svg class="h-6 w-6 block dark:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" /></svg>
                    <svg class="h-6 w-6 hidden dark:block" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" /></svg>
                </button>
            </header>
            <main class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    """

    if not repos:
        html_content += """
        <div class="col-span-full text-center py-12 bg-white dark:bg-gray-800 rounded-lg shadow-md">
            <h2 class="text-2xl font-bold text-gray-700 dark:text-gray-200">No Repositories Found</h2>
            <p class="text-gray-500 dark:text-gray-400 mt-2">The curator script did not find any repositories matching the criteria.</p>
        </div>
        """
    else:
        for repo in repos:
            topics_html = ''.join([f'<span class="inline-block bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300 text-xs font-semibold mr-2 mb-2 px-2.5 py-0.5 rounded-full">{topic}</span>' for topic in repo.get('topics', [])[:5]])
            html_content += f"""
            <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm hover:shadow-lg transition-shadow duration-300 p-6 flex flex-col">
                <div class="flex-grow">
                    <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100 mb-2">
                        <a href="{repo.get('html_url', '#')}" target="_blank" class="hover:text-blue-600 dark:hover:text-blue-400 transition-colors">{repo['name']}</a>
                    </h2>
                    <p class="text-gray-600 dark:text-gray-300 mb-4 text-sm h-24 overflow-auto">{repo.get('description', 'No description available.')}</p>
                </div>
                <div class="mt-auto pt-4 border-t border-gray-100 dark:border-gray-700">
                    <div class="h-14 overflow-y-auto mb-4">
                        {topics_html}
                    </div>
                    <div class="flex justify-between items-center text-sm text-gray-500 dark:text-gray-400">
                        <span class="font-semibold">⭐ {repo.get('stars', 0):,}</span>
                        <span class="font-semibold text-purple-600 dark:text-purple-400">{repo.get('language', 'N/A')}</span>
                        <span>Updated: {repo.get('last_updated', 'N/A')}</span>
                    </div>
                </div>
            </div>
            """

    html_content += """
            </main>
            <footer class="text-center mt-12 text-gray-500 dark:text-gray-400">
                <p>Generated by AI Repository Curator | <a href="https://github.com/hexacron/ai-curator" class="hover:text-blue-600 dark:hover:text-blue-400" target="_blank">View on GitHub</a></p>
            </footer>
        </div>
    </body>
    </html>
    """

    with open(os.path.join(docs_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ Successfully generated clean website at {os.path.join(docs_dir, 'index.html')}.")

if __name__ == "__main__":
    generate_website()
