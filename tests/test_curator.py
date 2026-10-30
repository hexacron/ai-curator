import json
import re
from datetime import date

import pytest

import curator
from tests.curator_fakes import HAIKU, SONNET, FakeGraphQL, FakeLLM, reply

TODAY = date.today().isoformat()
TEMPLATE_PATH = 'config_template.json'


def item(repo_id, name, stars, **overrides):
    """A GitHub search result that passes the curator's filters."""
    base = {'id': repo_id, 'node_id': f'N{repo_id}', 'name': name, 'full_name': f'o/{name}',
            'html_url': f'https://github.com/o/{name}', 'description': f'{name}: a tool that does a reasonably described job',
            'stargazers_count': stars, 'language': 'Python', 'pushed_at': f'{TODAY}T00:00:00Z', 'topics': ['ai'],
            'license': {'name': 'MIT'}, 'fork': False, 'size': 5000}
    base.update(overrides)
    return base


class CuratorGitHub(FakeGraphQL):
    """Stands in for GitHubClient: search results, GraphQL README answers, and a count of searches."""
    api_url = 'https://api.github.com'

    def __init__(self, items, readmes):
        super().__init__(readmes)
        self.items, self.searches = items, 0

    def validate_token(self):
        pass

    def get_json(self, url, params=None):
        self.searches += 1
        return {'items': self.items if params['page'] == 1 else []}


def by_name(mapping):
    """An LLM reply chooser: the reply registered for the repository whose name is in the prompt."""
    def respond(user):
        name = re.search(r'^name: (.+)$', user, re.M).group(1)
        return mapping.get(name, reply())
    return respond


def make(tmp_path, monkeypatch, items, llm, llm_config=None, caching=False):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    fake = CuratorGitHub(items, {i['node_id']: f"# {i['name']}\n\nRuns 150 security tools for pentest.\n" + 'More. ' * 20 for i in items})
    monkeypatch.setattr(curator, 'GitHubClient', lambda token: fake)
    config = {'search_queries': ['q'], 'advanced_options': {'api_delay_seconds': 0}, 'enable_caching': caching,
              'llm': llm_config or {}}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    return curator.GitHubCurator('config.json', llm=llm), fake


def readme_order(text):
    return re.findall(r'^### \d+\. \[([^\]]+)\]', text, re.M)


ITEMS = [item(1, 'tool_a', 100), item(2, 'tool_b', 5000), item(3, 'tool_c', 9000)]
REPLIES = {
    'tool_a': reply(summary='Core agent summary.'),
    'tool_b': reply(relevance='adjacent', category='security-tools', summary='Plain scanner summary.'),
    'tool_c': reply(relevance='off_topic', category='other', summary='A database tool.'),
}


def reviewing_llm():
    return FakeLLM({HAIKU: by_name(REPLIES), SONNET: by_name(REPLIES)})


def test_a_run_ranks_core_first_hides_confirmed_off_topic_and_writes_everything(tmp_path, monkeypatch):
    llm = reviewing_llm()
    app, _ = make(tmp_path, monkeypatch, ITEMS, llm)

    app.run()

    readme = (tmp_path / 'README.md').read_text(encoding='utf-8')
    assert readme_order(readme) == ['tool_a', 'tool_b']           # core first despite fewer stars; off-topic absent
    assert 'tool_c' not in readme and '**Hidden as off-topic**: 1' in readme
    assert 'Core agent summary.' in readme and '**Category**: AI for security' in readme
    assert '### 🗂️ Categories' in readme and 'Reviewed 3 of 3' in readme
    cached = json.loads((tmp_path / 'cache.json').read_text())['repositories']
    assert [r['name'] for r in cached] == ['tool_a', 'tool_b', 'tool_c']       # the site gets everything, ranked
    assert cached[2]['review']['relevance'] == 'off_topic' and cached[0]['review']['summary'] == 'Core agent summary.'
    assert len((tmp_path / 'data/curator/reviews.jsonl').read_text().splitlines()) == 4     # header + 3
    assert llm.models().count(HAIKU) == 3 and llm.models().count(SONNET) == 1


def test_without_a_client_nothing_changes_and_no_empty_store_is_created(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch, ITEMS, None)

    app.run()

    readme = (tmp_path / 'README.md').read_text(encoding='utf-8')
    assert readme_order(readme) == ['tool_c', 'tool_b', 'tool_a']      # plain star order, as before reviews existed
    assert 'Category' not in readme and 'language model' not in readme and 'Hidden' not in readme
    assert not (tmp_path / 'data').exists()


def test_stored_reviews_keep_applying_when_a_later_run_has_no_client(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch, ITEMS, reviewing_llm())[0].run()
    first = (tmp_path / 'README.md').read_text(encoding='utf-8')
    app, _ = make(tmp_path, monkeypatch, ITEMS, None)

    app.run()

    second = (tmp_path / 'README.md').read_text(encoding='utf-8')
    assert readme_order(second) == ['tool_a', 'tool_b'] and 'Hidden as off-topic' in second
    assert re.sub(r'\*Last updated.*\*', '', first) == re.sub(r'\*Last updated.*\*', '', second)


def test_disabled_in_config_means_no_calls_no_reviews_and_no_store(tmp_path, monkeypatch):
    llm = reviewing_llm()
    app, _ = make(tmp_path, monkeypatch, ITEMS, llm, llm_config={'enabled': False})

    app.run()

    assert llm.calls == [] and not (tmp_path / 'data').exists()
    assert readme_order((tmp_path / 'README.md').read_text(encoding='utf-8')) == ['tool_c', 'tool_b', 'tool_a']


def test_a_model_outage_degrades_to_the_old_behaviour_without_failing_the_run(tmp_path, monkeypatch):
    from llm_client import LLMError
    llm = FakeLLM(errors=[LLMError('down')] * 10)
    app, _ = make(tmp_path, monkeypatch, ITEMS, llm)

    app.run()

    assert readme_order((tmp_path / 'README.md').read_text(encoding='utf-8')) == ['tool_c', 'tool_b', 'tool_a']
    assert all(r['review'] is None for r in json.loads((tmp_path / 'cache.json').read_text())['repositories'])


def test_a_cache_hit_reviews_what_is_new_and_keeps_the_caches_original_age(tmp_path, monkeypatch):
    llm = reviewing_llm()
    app, fake = make(tmp_path, monkeypatch, ITEMS, llm, caching=True)
    app.run()
    first_stamp = json.loads((tmp_path / 'cache.json').read_text())['timestamp']
    calls_after_first = len(llm.calls)

    app2, fake2 = make(tmp_path, monkeypatch, ITEMS, llm, caching=True)
    app2.run()

    assert fake2.searches == 0                                              # served from the cache
    assert len(llm.calls) == calls_after_first                              # nothing new to review
    assert json.loads((tmp_path / 'cache.json').read_text())['timestamp'] == first_stamp


def test_markdown_in_descriptions_and_summaries_cannot_inject_links_images_html_or_lists(tmp_path, monkeypatch):
    hostile = '[click](http://evil.example) ![x](http://tracker.example/p.png) <img src=x onerror=1> # heading'
    items = [item(1, 'tool_a', 100, description=hostile), item(2, 'tool_b', 90, description='- looks like a list item and a second line\nnext')]
    replies = {'tool_a': reply(summary=hostile)}
    # a budget of one review: tool_a shows the model's summary, tool_b shows its raw description
    app, _ = make(tmp_path, monkeypatch, items, FakeLLM({HAIKU: by_name(replies), SONNET: by_name(replies)}),
                  llm_config={'max_calls_per_run': 1})

    app.run()

    readme = (tmp_path / 'README.md').read_text(encoding='utf-8')
    # nothing dangerous may remain *unescaped* (an escaped "\<img" renders as literal text)
    assert not re.search(r'(?<!\\)<', readme) and not re.search(r'(?<!\\)!\[', readme)
    assert not re.search(r'\]\(http://evil', readme) and not re.search(r'(?m)^# heading', readme)
    assert '\\[click\\]\\(http://evil.example\\)' in readme
    assert re.search(r'(?m)^\\- looks like a list item', readme) and not re.search(r'(?m)^- looks', readme)


def test_the_markdown_escaper_handles_leading_list_markers_and_collapses_whitespace():
    assert curator._md('  - a\n\n b ') == '\\- a b'
    assert curator._md('1. first') == '1\\. first'
    assert curator._md('plain text, with punctuation: ok') == 'plain text, with punctuation: ok'


def test_rank_key_orders_core_then_adjacent_or_unreviewed_then_off_topic(tmp_path, monkeypatch):
    app, _ = make(tmp_path, monkeypatch, ITEMS, None)

    def repo(stars, relevance):
        return curator.RepoInfo('n', 'o/n', 'u', 'd', stars, 'Python', '2026-10-10', [], None,
                                review={'relevance': relevance, 'category': 'osint'} if relevance else None)

    keys = {name: app._rank_key(repo(stars, relevance)) for name, stars, relevance in
            [('core_small', 10, 'core'), ('adjacent_big', 99999, 'adjacent'), ('unreviewed_big', 99999, None), ('off_big', 99999, 'off_topic')]}
    assert keys['core_small'] > keys['adjacent_big'] == keys['unreviewed_big'] > keys['off_big']


@pytest.mark.parametrize('llm_config', [{'unknown_key': 1}, {'effort': 'turbo'}, {'model': ''}, {'max_calls_per_run': -1},
                                        {'min_confidence_to_hide': 1.5}, {'retain_days': 0}, {'confirm_model': None}])
def test_bad_llm_settings_are_rejected_at_startup(tmp_path, monkeypatch, llm_config):
    with pytest.raises(ValueError):
        make(tmp_path, monkeypatch, ITEMS, None, llm_config=llm_config)


def test_the_config_template_lists_exactly_the_llm_defaults():
    with open(TEMPLATE_PATH, encoding='utf-8') as f:
        assert json.load(f)['llm'] == curator.LLM_DEFAULTS


def test_search_results_carry_ids_and_old_cache_entries_without_them_still_load():
    parsed = curator.RepoInfo.from_github_api(item(7, 'tool_g', 50))
    assert (parsed.repo_id, parsed.node_id, parsed.review) == (7, 'N7', None)

    old = {'name': 'n', 'full_name': 'o/n', 'html_url': 'u', 'description': None, 'stars': 1, 'language': 'Go',
           'last_updated': '2026-10-01', 'topics': [], 'license_name': None, 'is_fork': False, 'size_kb': 0}
    assert curator.RepoInfo(**old).repo_id == 0
