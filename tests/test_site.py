import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from osint_tracker import site

HOSTILE = '</script><script>alert(1)</script><!-- <img src=x onerror=alert(2)> --> & \u2028 end'


def entry(repo_id, sensitivity='none', description='A tool', **extra):
    base = {'rank': 1, 'rank_change': None, 'id': repo_id, 'full_name': f'o/r{repo_id}', 'html_url': 'https://github.com/o/r',
            'description': description, 'sensitivity': sensitivity, 'domain': 'social-media', 'score': 50.0}
    base.update(extra)
    return base


def boards(**overrides):
    base = {
        'version': 1, 'generated': '2026-10-10T12:00:00+00:00', 'window_days': 90, 'stats': {'candidates': 5},
        'has_previous': True,
        'boards': {'rising': [entry(1)], 'new': [], 'established': [], 'adjacent': [], 'domains': {'social-media': [entry(1)]}},
        'history': {'1': {'start': '2026-10-01', 'stars': [1, 2]}, '9': {'start': '2026-10-01', 'stars': [5, 6]}},
        'rejected': [{'id': 7, 'full_name': 'o/bad', 'reason': 'clone', 'why': 'x'}],
    }
    base.update(overrides)
    return base


def test_the_public_payload_drops_the_rejected_list_and_history_of_repos_not_shown():
    payload = site.public_payload(boards())

    assert 'rejected' not in payload
    assert set(payload['history']) == {'1'}
    assert payload['has_previous'] is True and payload['stats'] == {'candidates': 5}


def test_a_high_sensitivity_entry_never_reaches_the_page_even_if_a_board_contained_one():
    leaky = boards()
    leaky['boards']['rising'].append(entry(2, sensitivity='high', description='LEAKY'))
    leaky['boards']['domains']['social-media'].append(entry(2, sensitivity='high', description='LEAKY'))
    leaky['history']['2'] = {'start': '2026-10-01', 'stars': [1, 2]}

    payload = site.public_payload(leaky)

    assert [e['id'] for e in payload['boards']['rising']] == [1]
    assert [e['id'] for e in payload['boards']['domains']['social-media']] == [1]
    assert '2' not in payload['history'] and 'LEAKY' not in json.dumps(payload)


def test_embedded_json_cannot_end_the_script_element_or_start_a_comment_and_still_round_trips():
    payload = {'text': HOSTILE, 'nested': [HOSTILE]}

    text = site.embed_json(payload)

    assert not any(token in text for token in ('<', '>', '&', '\u2028'))
    assert json.loads(text) == payload


def test_the_page_contains_the_data_once_and_hostile_text_cannot_break_out_of_it():
    page = site.render(site.public_payload(boards(boards={'rising': [entry(1, description=HOSTILE)], 'new': [], 'established': [],
                                                          'adjacent': [], 'domains': {}})))

    data_block = re.search(r'<script type="application/json" id="osint-data">(.*?)</script>', page, re.S).group(1)
    assert '</script>' not in data_block and '<img' not in data_block
    assert page.count('<script') == 4  # theme, data, logic, ui: injected text added none


def test_text_that_looks_like_a_template_placeholder_is_left_alone():
    page = site.render(site.public_payload(boards(boards={'rising': [entry(1, description='/*__LOGIC__*/ /*__UI__*/')], 'new': [],
                                                          'established': [], 'adjacent': [], 'domains': {}})))

    assert page.count('/*__LOGIC__*/') == 1 and page.count('/*__UI__*/') == 1  # only inside the data
    assert 'const OsintLogic' in page and 'getElementById(\'osint-data\')' in page


def test_the_page_is_self_contained_and_forbids_other_origins():
    page = site.render(site.public_payload(boards()))

    assert "default-src 'none'" in page and 'http-equiv="Content-Security-Policy"' in page
    assert not re.findall(r'<link\b|<script[^>]*\bsrc=|@import|url\(\s*[\'"]?https?:|\b(?:src|href)=["\']https?://', page)
    assert 'innerHTML' not in page and 'document.write' not in page and 'eval(' not in page
    assert '/*__' not in page.replace('/*__LOGIC__*/', '')  # no unreplaced placeholder (data never contains one here)


def test_generate_writes_the_page_and_its_data(tmp_path):
    data_dir, out = tmp_path / 'data', tmp_path / 'out'
    data_dir.mkdir()
    (data_dir / 'boards.json').write_text(json.dumps(boards()), encoding='utf-8')

    page = site.generate(str(data_dir), str(out))

    assert page == out / 'index.html' and 'Emerging OSINT Radar' in page.read_text(encoding='utf-8')
    published = json.loads((out / 'data.json').read_text(encoding='utf-8'))
    assert published == site.public_payload(boards())


def test_a_missing_or_corrupt_boards_file_stops_the_build_instead_of_publishing_an_empty_page(tmp_path):
    with pytest.raises(SystemExit):
        site.generate(str(tmp_path), str(tmp_path / 'out'))

    (tmp_path / 'boards.json').write_text('{"boards": ', encoding='utf-8')
    with pytest.raises(SystemExit):
        site.generate(str(tmp_path), str(tmp_path / 'out'))
    assert not (tmp_path / 'out' / 'index.html').exists()


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed (CI has it)')
def test_the_pages_pure_logic_passes_its_javascript_checks():
    script = Path(__file__).parent / 'js' / 'logic_check.js'

    result = subprocess.run(['node', str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
