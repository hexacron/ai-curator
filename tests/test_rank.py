from osint_tracker import rank
from osint_tracker.config import load_config
from tests.helpers import NOW, days_ago, ts

CONFIG = load_config('does-not-exist.json')


def scored(repo_id, owner=None, value=50.0, scope='core', sensitivity='none', status='emerging', confidence=0.8,
           integrity=1.0, domain='social-media', age=20, **overrides):
    rec = {
        'id': repo_id, 'full_name': f'{owner or f"owner{repo_id}"}/tool{repo_id}', 'owner': owner or f'owner{repo_id}',
        'html_url': f'https://github.com/x/{repo_id}', 'description': 'd', 'language': 'Python', 'license': 'MIT',
        'topics': [], 'created_at': ts(days_ago(age)), 'stars': [10], 'forks': [1], 'snap_start': '2026-10-10',
        'tier1': True, 'gone': False, 'archived': False, 'clone_of': None,
        'class': {'scope': scope, 'domain': domain, 'domain_secondary': None, 'form': 'cli', 'ai_role': 'none',
                  'sensitivity': sensitivity, 'sensitivity_tags': [], 'why': 'x=2'},
        'score': {'status': status, 'components': {}, 'integrity': {'mult': integrity, 'flags': []},
                  'confidence': confidence, 'totals': {'rising': value, 'overall': value},
                  'rank_keys': {'rising': value, 'overall': value}, 'star_rate': 1.0},
    }
    rec.update(overrides)
    return rec


def build(records, previous=None):
    return rank.build_boards(records, NOW, CONFIG, previous or {})


def ids(board):
    return [entry['id'] for entry in board]


def test_boards_are_ordered_by_rank_key_and_owner_capped():
    records = [scored(1, 'mallory', 90), scored(2, 'mallory', 80), scored(3, 'mallory', 70), scored(4, 'bob', 60)]

    rising = build(records)['boards']['rising']

    assert ids(rising) == [1, 2, 4]
    assert [e['rank'] for e in rising] == [1, 2, 3]


def test_owner_cap_ignores_case():
    records = [scored(1, 'Mallory', 90), scored(2, 'mallory', 80), scored(3, 'MALLORY', 70)]

    assert ids(build(records)['boards']['rising']) == [1, 2]


def test_high_sensitivity_is_on_no_board_but_is_counted():
    result = build([scored(1, sensitivity='high'), scored(2)])

    domain_entries = [e for board in result['boards']['domains'].values() for e in board]
    on_any_board = [e for name, board in result['boards'].items() if name != 'domains' for e in board] + domain_entries
    assert ids(on_any_board) == [2, 2]  # only the non-sensitive repo: on Rising and on its domain board
    assert result['excluded']['sensitivity_high_hidden'] == 1


def test_caution_repositories_are_on_rising_and_every_other_board_with_their_label_but_high_never_is():
    result = build([scored(1, sensitivity='caution', age=3), scored(2, sensitivity='high', age=3)])

    assert ids(result['boards']['rising']) == [1]
    assert ids(result['boards']['new']) == [1]
    assert result['boards']['rising'][0]['sensitivity'] == 'caution'
    assert result['boards']['domains']['social-media'][0]['sensitivity'] == 'caution'


def test_unverified_clone_and_out_of_scope_repos_are_excluded_and_explained():
    records = [scored(1, integrity=0.6), scored(2, clone_of=9), scored(3, scope='out', score=None), scored(4)]
    del records[2]['score']

    result = build(records)

    assert ids(result['boards']['rising']) == [4]
    assert result['excluded'] == {'unverified': 1, 'clone': 1, 'scope_out': 1}
    assert {(r['id'], r['reason']) for r in result['rejected']} == {(2, 'clone'), (3, 'out of scope')}


def test_untracked_gone_and_archived_repos_are_ignored_silently():
    records = [scored(1, gone=True), scored(2, archived=True), scored(3, tier1=False), scored(4)]

    assert ids(build(records)['boards']['rising']) == [4]


def test_adjacent_scope_only_appears_on_the_adjacent_board():
    result = build([scored(1, scope='adjacent', domain='infrastructure-asm'), scored(2)])

    assert ids(result['boards']['adjacent']) == [1]
    assert 1 not in ids(result['boards']['rising']) and 'infrastructure-asm' not in result['boards']['domains']


def test_established_repos_are_not_rising_or_new_and_get_their_own_board():
    result = build([scored(1, status='established', age=200), scored(2)])

    assert ids(result['boards']['established']) == [1]
    assert ids(result['boards']['rising']) == [2]


def test_rising_needs_confidence_but_new_does_not():
    result = build([scored(1, confidence=0.4, age=3)])

    assert ids(result['boards']['rising']) == []
    assert ids(result['boards']['new']) == [1]


def test_new_board_is_limited_to_the_last_week():
    result = build([scored(1, age=6), scored(2, age=8)])

    assert ids(result['boards']['new']) == [1]


def test_domain_boards_group_by_domain():
    result = build([scored(1, domain='social-media'), scored(2, domain='geospatial-imagery'), scored(3, domain=None)])

    assert {d: ids(b) for d, b in result['boards']['domains'].items()} == {'social-media': [1], 'geospatial-imagery': [2]}


def test_rank_change_is_previous_rank_minus_current_and_null_for_new_entries():
    records = [scored(1, value=90), scored(2, value=80), scored(3, value=70)]
    previous = {'rising': {1: 3, 2: 2}}  # repo 1 was third, repo 2 second, repo 3 not on the board

    rising = {e['id']: e for e in build(records, previous)['boards']['rising']}

    assert rising[1]['rank_change'] == 2   # climbed from 3 to 1
    assert rising[2]['rank_change'] == 0
    assert rising[3]['rank_change'] is None


def test_previous_ranks_round_trip_through_the_saved_file(tmp_path):
    result = build([scored(1, value=90), scored(2, value=80, domain='geospatial-imagery')])
    rank.save(str(tmp_path), result)

    previous = rank.load_previous_ranks(str(tmp_path))

    assert previous['rising'] == {1: 1, 2: 2}
    assert previous['domains/geospatial-imagery'] == {2: 1}
    assert rank.load_previous_ranks(str(tmp_path / 'missing')) == {}


def test_the_programming_language_survives_next_to_the_readme_language():
    rec = scored(1)
    rec['class']['language'] = 'fr'

    entry = build([rec])['boards']['rising'][0]

    assert entry['language'] == 'Python' and entry['readme_language'] == 'fr'


def test_history_holds_the_last_90_days_of_stars_for_repos_on_a_board_only():
    long_series = list(range(100))
    on_board = scored(1, stars=long_series, snap_start='2026-06-01')
    off_board = scored(2, scope='out')
    on_board['stars'] = long_series
    off_board['stars'] = [1, 2]
    off_board['snap_start'] = '2026-10-01'

    result = build([on_board, off_board])

    assert set(result['history']) == {'1'}
    assert result['history']['1']['stars'] == long_series[-90:]
    assert result['history']['1']['start'] == '2026-06-11'  # 10 dropped days after 2026-06-01


def test_has_previous_tells_the_site_whether_rank_changes_mean_anything():
    assert build([scored(1)])['has_previous'] is False
    assert build([scored(1)], previous={'rising': {1: 1}})['has_previous'] is True
