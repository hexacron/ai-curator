import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

import generate_website as gw

HOSTILE = '</script><script>alert(1)</script><img src=x onerror=alert(2)> "quoted" & more'


class TagCollector(HTMLParser):
    """The elements and attributes a browser would actually create from a piece of markup."""

    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)


def repo(**overrides):
    base = {'name': 'tool', 'html_url': 'https://github.com/o/tool', 'description': 'A plain description', 'stars': 1234,
            'language': 'Python', 'last_updated': '2026-10-10', 'topics': ['ai', 'security'], 'review': None}
    base.update(overrides)
    return base


def review(**overrides):
    base = {'relevance': 'core', 'category': 'ai-for-security', 'summary': 'Model summary.', 'utility': 4, 'model': 'm'}
    base.update(overrides)
    return base


def test_an_unreviewed_card_looks_as_it_did_before_reviews_existed():
    card = gw._render_repo(repo())

    assert 'A plain description' in card and 'chip-cat' not in card and 'Docs ' not in card
    assert 'hidden' not in card and 'data-off' not in card and 'data-cat=""' in card


def test_a_reviewed_card_shows_the_summary_the_category_and_the_documentation_rating():
    card = gw._render_repo(repo(review=review()))

    assert 'Model summary.' in card and 'A plain description' not in card
    assert '<span class="chip chip-cat">AI for security</span>' in card and 'data-cat="ai-for-security"' in card
    assert 'Docs 4/5' in card


def test_a_missing_rating_shows_no_docs_badge_and_zero_is_a_real_rating():
    assert 'Docs ' not in gw._render_repo(repo(review=review(utility=None)))
    assert 'Docs 0/5' in gw._render_repo(repo(review=review(utility=0)))


def test_off_topic_cards_are_hidden_until_asked_for():
    card = gw._render_repo(repo(review=review(relevance='off_topic', category='other')))

    assert 'data-off="1" hidden' in card


def test_nothing_from_the_repository_or_the_model_can_break_out_of_its_html_context():
    hostile_review = review(summary=HOSTILE, category='x" onmouseover="alert(3)')
    card = gw._render_repo(repo(name=HOSTILE, description=HOSTILE, topics=[HOSTILE], language=HOSTILE,
                                html_url='javascript:alert(4)', review=hostile_review))

    parser = TagCollector()
    parser.feed(card)
    assert set(parser.tags) <= {'div', 'h2', 'a', 'p', 'span'}                      # injected <script>/<img> became text
    assert not [name for name, _ in parser.attrs if name.startswith('on')]           # no event-handler attributes
    assert [value for name, value in parser.attrs if name == 'href'] == ['#']        # javascript: link neutralised
    assert [value for name, value in parser.attrs if name == 'data-cat'] == ['x" onmouseover="alert(3)']  # one attribute, intact


def test_the_filter_bar_counts_only_what_is_shown_by_default_and_offers_the_off_topic_toggle_when_needed():
    repos = [repo(review=review()), repo(review=review()), repo(review=review(category='osint', relevance='adjacent')),
             repo(review=review(relevance='off_topic', category='other'))]

    bar = gw._render_filters(repos)

    assert 'All (3)' in bar and 'AI for security (2)' in bar and 'OSINT (1)' in bar and 'Other' not in bar
    assert 'Show 1 off-topic' in bar and 'id="show-off"' in bar
    assert 'Show ' not in gw._render_filters([repo(review=review())])


def test_without_any_review_there_is_no_filter_bar():
    assert gw._render_filters([repo(), repo()]) == ''


def write_cache(tmp_path, repos):
    (tmp_path / 'cache.json').write_text(json.dumps({'timestamp': 0, 'config_hash': 'x', 'repositories': repos}), encoding='utf-8')


def test_the_generated_page_is_self_contained_filters_in_the_browser_and_marks_model_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_cache(tmp_path, [repo(name='a', review=review()), repo(name='b', review=review(relevance='adjacent', category='osint')),
                           repo(name='c', review=review(relevance='off_topic', category='other'))])

    gw.generate_website()

    page = (tmp_path / 'docs' / 'index.html').read_text(encoding='utf-8')
    assert "default-src 'none'" in page and 'http-equiv="Content-Security-Policy"' in page
    assert not re.findall(r'<link\b|<script[^>]*\bsrc=|@import|url\(\s*[\'"]?https?:|\b(?:src)=["\']https?://', page)
    assert 'innerHTML' not in page and 'eval(' not in page and 'document.write' not in page
    assert page.count(gw.FILTER_JS.strip()) == 1 and 'Reviewed 3 of 3' in page and 'written by a language model' in page
    assert len(re.findall(r'<div class="card"[^>]* hidden>', page)) == 1 and 'Show 1 off-topic' in page


def test_a_page_without_reviews_has_no_filter_bar_or_model_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_cache(tmp_path, [repo(name='a'), repo(name='b')])

    gw.generate_website()

    page = (tmp_path / 'docs' / 'index.html').read_text(encoding='utf-8')
    assert 'class="filters"' not in page and 'language model' not in page and page.count('<div class="card"') == 2


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed (CI has it)')
def test_the_filter_script_is_valid_javascript(tmp_path):
    script = tmp_path / 'filter.js'
    script.write_text(gw.FILTER_JS, encoding='utf-8')

    result = subprocess.run(['node', '--check', str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
