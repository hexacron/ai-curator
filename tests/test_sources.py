import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import curator
import generate_website as gw
import sources
from sources import SOURCE_DEFAULTS, SourceError, SourceItem, merge_source_settings
from tests.test_curator import ITEMS, make

RECENT = (date.today() - timedelta(days=5)).isoformat()
OLD = (date.today() - timedelta(days=900)).isoformat()


class FakeSession:
    """Answers each GET from `answers` (a callable(url, params) -> response-like)."""

    def __init__(self, answer):
        self.answer, self.calls, self.headers = answer, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.answer(url, params)


def ok(body=None, text=''):
    return SimpleNamespace(status_code=200, headers={}, text=text, json=lambda: body)


def hf_settings(**overrides):
    settings = dict(SOURCE_DEFAULTS['huggingface'], queries=['q'], kinds=['model', 'dataset'], api_delay_seconds=0)
    settings.update(overrides)
    return settings


def atom(*entries):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(entries) + '</feed>')


def paper(arxiv_id='2610.41998v2', title='A  Title\n with breaks', published=f'{RECENT}T10:00:00Z'):
    return (f'<entry><id>http://arxiv.org/abs/{arxiv_id}</id><published>{published}</published><title>{title}</title>'
            '<summary>  Abstract\n text. </summary><author><name>Ada L</name></author>'
            '<category term="cs.CR"/><category term="cs.AI"/></entry>')


def test_huggingface_keeps_liked_recent_items_dedupes_and_orders_by_likes():
    def answer(url, params):
        if url.endswith('/models'):
            return ok([{'id': 'a/popular', 'likes': 50, 'downloads': 9, 'lastModified': f'{RECENT}T00:00:00.000Z',
                        'tags': ['transformers', 'osint', 'license:mit', 'region:us'], 'pipeline_tag': 'text-classification'},
                       {'id': 'a/unliked', 'likes': 1, 'lastModified': f'{RECENT}T00:00:00.000Z'},
                       {'id': 'a/stale', 'likes': 99, 'lastModified': f'{OLD}T00:00:00.000Z'}])
        return ok([{'id': 'b/data', 'likes': 80, 'description': ' Rows\n of data ', 'lastModified': f'{RECENT}T00:00:00.000Z'}])

    items = sources.search_huggingface(hf_settings(queries=['q', 'q2']), FakeSession(answer))   # q2 repeats every hit

    assert [i.id for i in items] == ['b/data', 'a/popular']
    data, model = items
    assert data.url == 'https://huggingface.co/datasets/b/data' and data.summary == 'Rows of data' and data.kind == 'dataset'
    assert model.url == 'https://huggingface.co/a/popular'
    assert model.tags == ['text-classification', 'transformers', 'osint']      # namespaced tags (license:, region:) dropped


def test_huggingface_asks_only_for_fields_each_kind_has():
    session = FakeSession(lambda url, params: ok([]))
    sources.search_huggingface(hf_settings(kinds=['model', 'dataset', 'space']), session)

    fields = {url.rsplit('/', 1)[1]: [v for k, v in params if k == 'expand[]'] for url, params in session.calls}
    assert 'downloads' not in fields['spaces'] and 'sdk' in fields['spaces']
    assert 'description' in fields['datasets'] and 'description' not in fields['models']


def test_arxiv_parses_entries_strips_the_version_and_collapses_whitespace():
    [parsed] = sources.parse_arxiv(atom(paper()))

    assert (parsed.id, parsed.url) == ('2610.41998', 'https://arxiv.org/abs/2610.41998')
    assert parsed.title == 'A Title with breaks' and parsed.summary == 'Abstract text.'
    assert parsed.tags == ['cs.CR', 'cs.AI'] and parsed.authors == ['Ada L'] and parsed.date == RECENT


def test_arxiv_reports_a_rejected_query_and_broken_xml_instead_of_returning_nothing():
    error = '<entry><id>http://arxiv.org/api/errors#bad</id><summary>syntax error</summary></entry>'
    with pytest.raises(SourceError, match='syntax error'):
        sources.parse_arxiv(atom(error))
    with pytest.raises(SourceError):
        sources.parse_arxiv('<feed')


def test_arxiv_search_scopes_to_categories_drops_old_papers_and_lists_newest_first():
    older = (date.today() - timedelta(days=20)).isoformat()
    body = atom(paper('2601.00001v1', published=f'{older}T00:00:00Z'), paper('2601.00002v1'),
                paper('2001.00003v1', published=f'{OLD}T00:00:00Z'))
    session = FakeSession(lambda url, params: ok(text=body))
    settings = dict(SOURCE_DEFAULTS['arxiv'], queries=['all:OSINT'], categories=['cs.CR', 'cs.AI'], api_delay_seconds=0)

    items = sources.search_arxiv(settings, session)

    assert [i.id for i in items] == ['2601.00002', '2601.00001']
    assert session.calls[0][1]['search_query'] == '(all:OSINT) AND (cat:cs.CR OR cat:cs.AI)'


def test_a_failing_source_raises_after_retries_so_nothing_partial_is_published(monkeypatch):
    monkeypatch.setattr(sources.time, 'sleep', lambda s: None)
    session = FakeSession(lambda url, params: SimpleNamespace(status_code=503, headers={}, text='down'))
    with pytest.raises(SourceError):
        sources.search_arxiv(dict(SOURCE_DEFAULTS['arxiv'], queries=['x']), session)
    assert len(session.calls) == sources.MAX_RETRIES + 1

    not_found = FakeSession(lambda url, params: SimpleNamespace(status_code=404, headers={}, text='no'))
    with pytest.raises(SourceError):
        sources.search_arxiv(dict(SOURCE_DEFAULTS['arxiv'], queries=['x']), not_found)
    assert len(not_found.calls) == 1                                  # a 404 is not retried


def test_rate_limit_retry_after_is_honoured_up_to_a_cap():
    def response(status, retry_after=None):
        return SimpleNamespace(status_code=status, headers={'Retry-After': retry_after} if retry_after else {})

    assert sources._retry_delay(response(429, '7'), 0) == 7.0
    assert sources._retry_delay(response(429, str(sources.MAX_WAIT_SECONDS + 1)), 0) is None
    assert sources._retry_delay(response(500), 1) == 4.0


@pytest.mark.parametrize('override', [{'nope': {}}, {'arxiv': {'quries': []}}, {'arxiv': []}, {'arxiv': {'queries': 'x'}},
                                      {'huggingface': {'kinds': ['video']}}, {'huggingface': {'limit_per_query': 0}},
                                      {'arxiv': {'max_age_days': -1}}])
def test_bad_source_settings_are_rejected(override):
    with pytest.raises(ValueError):
        merge_source_settings(override)


def test_source_settings_merge_per_source_without_losing_sibling_defaults():
    merged = merge_source_settings({'arxiv': {'enabled': False, 'queries': [' a ', 'a', '']}})

    assert merged['arxiv']['enabled'] is False and merged['arxiv']['queries'] == ['a']
    assert merged['arxiv']['categories'] == SOURCE_DEFAULTS['arxiv']['categories']
    assert merged['huggingface'] == SOURCE_DEFAULTS['huggingface']
    assert SOURCE_DEFAULTS['arxiv']['enabled'] is True                 # the defaults themselves are never mutated


HOSTILE = '[x](http://evil.example) <img src=x onerror=1>'
FOUND = [SourceItem('huggingface', 'model', 'a/m', 'a/m', 'https://huggingface.co/a/m', date=RECENT, likes=12, downloads=3400,
                    tags=['osint']),
         SourceItem('arxiv', 'paper', '2601.00002', HOSTILE, 'https://arxiv.org/abs/2601.00002', summary=HOSTILE, date=RECENT,
                    tags=['cs.CR'], authors=['Ada L'])]


def test_a_run_publishes_items_in_readme_cache_and_site_and_escapes_their_text(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch, ITEMS, None)
    monkeypatch.setattr(curator, 'collect_items', lambda settings: list(FOUND))

    app.run()

    readme = (tmp_path / 'README.md').read_text(encoding='utf-8')
    assert '## 🤗 Hugging Face' in readme and '### 1. [a/m](https://huggingface.co/a/m)' in readme
    assert '**12 ❤** | **Model** | 3,400 downloads' in readme and '## 📄 arXiv Papers' in readme
    assert '\\[x\\]\\(http://evil.example\\)' in readme and '<img' not in readme.replace('\\<img', '')
    cached = json.loads((tmp_path / 'cache.json').read_text())['items']
    assert [i['id'] for i in cached] == ['a/m', '2601.00002']

    gw.generate_website()
    page = (tmp_path / 'docs' / 'index.html').read_text(encoding='utf-8')
    assert '<h2 class="section-title">Hugging Face</h2>' in page and '<h2 class="section-title">arXiv Papers</h2>' in page
    assert '<img' not in page and 'https://arxiv.org/abs/2601.00002' in page


def test_a_cache_hit_serves_items_without_calling_the_sources(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch, ITEMS, None, caching=True)
    monkeypatch.setattr(curator, 'collect_items', lambda settings: list(FOUND))
    app.run()
    app2, fake = make(tmp_path, monkeypatch, ITEMS, None, caching=True)
    monkeypatch.setattr(curator, 'collect_items', lambda settings: pytest.fail('sources were called on a cache hit'))

    app2.run()

    assert fake.searches == 0 and '## 📄 arXiv Papers' in (tmp_path / 'README.md').read_text(encoding='utf-8')


def test_a_source_failure_fails_the_run_before_anything_is_written(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch, ITEMS, None)

    def fail(settings):
        raise SourceError('arXiv down')

    monkeypatch.setattr(curator, 'collect_items', fail)
    with pytest.raises(SourceError):
        app.run()
    assert not (tmp_path / 'README.md').exists() and not (tmp_path / 'cache.json').exists()


def test_disabling_both_sources_in_config_skips_them(monkeypatch):
    called = []
    monkeypatch.setattr(sources, 'search_huggingface', lambda s: called.append('hf') or [])
    monkeypatch.setattr(sources, 'search_arxiv', lambda s: called.append('arxiv') or [])

    result = sources.collect_items(merge_source_settings({'huggingface': {'enabled': False}, 'arxiv': {'enabled': False}}))

    assert result == [] and called == []


def test_the_config_template_lists_exactly_the_source_defaults():
    with open('config_template.json', encoding='utf-8') as f:
        assert json.load(f)['sources'] == SOURCE_DEFAULTS
