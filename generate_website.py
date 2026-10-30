import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from curator_review import category_label

CACHE_FILE = 'cache.json'

CSS = """
:root { --bg:#f3f4f6; --card:#fff; --border:#e5e7eb; --divider:#f3f4f6; --title:#111827; --heading:#1f2937;
        --text:#4b5563; --muted:#6b7280; --faint:#9ca3af; --link:#2563eb; --lang:#9333ea;
        --chip-bg:#dbeafe; --chip-fg:#1e40af; --toggle-bg:#e5e7eb; }
html.dark { --bg:#111827; --card:#1f2937; --border:#374151; --divider:#374151; --title:#f3f4f6; --heading:#f3f4f6;
            --text:#d1d5db; --muted:#9ca3af; --faint:#6b7280; --link:#60a5fa; --lang:#c084fc;
            --chip-bg:#1e3a8a; --chip-fg:#93c5fd; --toggle-bg:#374151; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); transition:background .3s, color .3s;
       font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a { color:inherit; text-decoration:none; transition:color .3s; }
a:hover { color:var(--link); }
.container { max-width:1280px; margin:0 auto; padding:32px 16px; }
header { text-align:center; margin-bottom:40px; position:relative; }
h1 { margin:0; font-size:2.25rem; color:var(--heading); }
header p { margin:8px 0 0; }
header .stamp { font-size:.875rem; color:var(--faint); margin-top:4px; }
#theme-toggle { position:absolute; top:0; right:0; padding:8px; border:0; border-radius:50%; cursor:pointer;
                background:var(--toggle-bg); color:var(--heading); font-size:1.25rem; line-height:1; }
main { display:grid; gap:24px; grid-template-columns:1fr; }
@media (min-width:768px) { main { grid-template-columns:repeat(2,1fr); } }
@media (min-width:1024px) { main { grid-template-columns:repeat(3,1fr); } h1 { font-size:3rem; } }
.card { display:flex; flex-direction:column; background:var(--card); border:1px solid var(--border); border-radius:8px;
        padding:24px; box-shadow:0 1px 2px rgba(0,0,0,.06); transition:box-shadow .3s; }
.card:hover { box-shadow:0 10px 15px rgba(0,0,0,.15); }
.card h2 { margin:0 0 8px; font-size:1.25rem; color:var(--title); overflow-wrap:anywhere; }
.card .desc { flex-grow:1; margin:0 0 16px; height:6rem; overflow:auto; font-size:.875rem; }
.card .meta { padding-top:16px; border-top:1px solid var(--divider); }
.topics { height:3.5rem; overflow-y:auto; margin-bottom:16px; }
.chip { display:inline-block; background:var(--chip-bg); color:var(--chip-fg); font-size:.75rem; font-weight:600;
        margin:0 8px 8px 0; padding:2px 10px; border-radius:9999px; }
.stats { display:flex; justify-content:space-between; align-items:center; font-size:.875rem; color:var(--muted); }
.stats .lang { font-weight:600; color:var(--lang); }
.empty { grid-column:1/-1; text-align:center; padding:48px 16px; background:var(--card); border-radius:8px; }
footer { text-align:center; margin-top:48px; color:var(--muted); }
"""

THEME_JS = """
function applyTheme(theme) { document.documentElement.classList.toggle('dark', theme === 'dark'); }
function toggleTheme() {
    const newTheme = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
    localStorage.setItem('theme', newTheme);
    applyTheme(newTheme);
}
applyTheme(localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
"""

FILTER_CSS = """
.card[hidden] { display:none; }
.filters { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; align-items:center; margin:-16px 0 28px; }
.pill { border:1px solid var(--border); background:var(--card); color:var(--text); padding:6px 14px; border-radius:9999px;
        cursor:pointer; font:inherit; font-size:.8125rem; font-weight:600; }
.pill[aria-pressed="true"] { background:var(--link); border-color:var(--link); color:#fff; }
.check { font-size:.8125rem; color:var(--muted); display:inline-flex; gap:6px; align-items:center; cursor:pointer; margin-left:8px; }
.chip-cat { background:var(--link); color:#fff; }
.stats span[title] { cursor:help; }
"""

# Filtering runs in the browser; without it the page shows every card except the off-topic ones, which carry `hidden`.
FILTER_JS = """
(function () {
  const cards = Array.from(document.querySelectorAll('.card[data-cat]'));
  const pills = Array.from(document.querySelectorAll('.pill'));
  const showOff = document.getElementById('show-off');
  let category = '';
  function apply() {
    cards.forEach((card) => {
      const matches = !category || card.dataset.cat === category;
      card.hidden = !(matches && (card.dataset.off !== '1' || (showOff && showOff.checked)));
    });
  }
  pills.forEach((pill) => pill.addEventListener('click', () => {
    category = pill.dataset.cat;
    pills.forEach((other) => other.setAttribute('aria-pressed', other === pill ? 'true' : 'false'));
    apply();
  }));
  if (showOff) showOff.addEventListener('change', apply);
})();
"""


def _safe_url(url):
    """Allow only absolute http(s) URLs; anything else (javascript:, data:, ...) becomes '#'."""
    parsed = urlparse(url or '')
    return url if parsed.scheme in ('http', 'https') and parsed.netloc else '#'


def _is_hidden(raw_repo):
    """Reviewed as off-topic (confirmed and confident): hidden until the visitor asks for it."""
    return (raw_repo.get('review') or {}).get('relevance') == 'off_topic'


def _render_repo(raw_repo):
    """Render one repository card. Every field is untrusted GitHub data or model output derived from it, and is escaped."""
    review = raw_repo.get('review') or {}
    topics = raw_repo.get('topics') or []
    category = str(review.get('category') or '')
    chips = f'<span class="chip chip-cat">{escape(category_label(category))}</span>' if category else ''
    chips += ''.join(f'<span class="chip">{escape(str(topic))}</span>' for topic in topics[:5])
    stars = int(raw_repo.get('stars') or 0)
    text = review.get('summary') or raw_repo.get('description') or 'No description available.'
    utility = review.get('utility')
    docs = (f'<span title="How concretely the README shows a working tool (rated by a language model)">'
            f'Docs {int(utility)}/5</span>') if utility is not None else ''
    attrs = f' data-cat="{escape(category)}"' + (' data-off="1" hidden' if _is_hidden(raw_repo) else '')
    return f"""<div class="card"{attrs}>
<h2><a href="{escape(_safe_url(raw_repo.get('html_url')))}" target="_blank" rel="noopener noreferrer">{escape(str(raw_repo.get('name') or 'Unknown'))}</a></h2>
<p class="desc">{escape(str(text))}</p>
<div class="meta">
<div class="topics">{chips}</div>
<div class="stats"><span>⭐ {stars:,}</span><span class="lang">{escape(str(raw_repo.get('language') or 'N/A'))}</span>{docs}<span>Updated: {escape(str(raw_repo.get('last_updated') or 'N/A'))}</span></div>
</div>
</div>
"""


def _render_filters(repos):
    """Category pills (with counts of the cards shown by default) and the off-topic toggle."""
    shown = [repo for repo in repos if not _is_hidden(repo)]
    hidden = len(repos) - len(shown)
    counts = Counter(str(repo['review']['category']) for repo in shown if repo.get('review'))
    if not counts and not hidden:
        return ''
    pills = f'<button type="button" class="pill" data-cat="" aria-pressed="true">All ({len(shown)})</button>'
    pills += ''.join(
        f'<button type="button" class="pill" data-cat="{escape(category)}" aria-pressed="false">'
        f'{escape(category_label(category))} ({count})</button>'
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    toggle = f'<label class="check"><input type="checkbox" id="show-off"> Show {hidden} off-topic</label>' if hidden else ''
    return f'<nav class="filters" aria-label="Filter by category">{pills}{toggle}</nav>\n'


def generate_website():
    """
    Generates a self-contained HTML page (inline CSS/JS, no external requests)
    from cache.json, in the order the curator ranked the repositories.
    """
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            repos = json.load(f)['repositories']
    except (OSError, ValueError, KeyError) as e:
        sys.exit(f"❌ Could not read repositories from {CACHE_FILE}: {e}")

    print(f"✅ Rendering {len(repos)} repositories from {CACHE_FILE}.")

    docs_dir = 'docs'
    os.makedirs(docs_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    github_repo = os.getenv('GITHUB_REPOSITORY', 'hexacron/ai-curator')

    if repos:
        cards = ''.join(_render_repo(repo) for repo in repos)
    else:
        cards = """<div class="empty"><h2>No Repositories Found</h2>
<p>The curator script did not find any repositories matching the criteria.</p></div>
"""

    reviewed = sum(1 for repo in repos if repo.get('review'))
    note = ('<p>Summaries, categories and relevance are written by a language model from each project\'s README '
            f'and can be wrong. Reviewed {reviewed} of {len(repos)}.</p>\n') if reviewed else ''

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- The page is self-contained: the browser itself refuses any request to another origin. -->
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>AI &amp; OSINT Repository Curator</title>
<style>{CSS}{FILTER_CSS}</style>
<script>{THEME_JS}</script>
</head>
<body>
<div class="container">
<header>
<h1>AI &amp; OSINT Repository Curator</h1>
<p>A curated list of top-tier projects in AI, OSINT, and Cybersecurity.</p>
<p class="stamp">Last updated: {timestamp}</p>
<p><a href="osint/" style="color:var(--link)">Emerging OSINT Radar: newly created projects, ranked &rarr;</a></p>
<button id="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">◐</button>
</header>
{_render_filters(repos)}<main>
{cards}</main>
<footer>
{note}<p>Generated by AI Repository Curator | <a href="https://github.com/{escape(github_repo)}" target="_blank" rel="noopener noreferrer">View on GitHub</a></p>
</footer>
</div>
<script>{FILTER_JS}</script>
</body>
</html>
"""

    with open(os.path.join(docs_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ Successfully generated website at {os.path.join(docs_dir, 'index.html')}.")


if __name__ == "__main__":
    generate_website()
