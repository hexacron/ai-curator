import json

import pytest

from osint_tracker import curation

RECORD = {'id': 77, 'full_name': 'Alice/Tool', 'names': ['alice/old-name']}
KLASS = {'scope': 'core', 'domain': 'social-media', 'domain_secondary': 'identity-lookup', 'form': 'cli',
         'ai_role': 'none', 'sensitivity': 'high', 'sensitivity_tags': ['probiv']}


def write(tmp_path, content):
    (tmp_path / 'curation.json').write_text(json.dumps(content), encoding='utf-8')
    return curation.load(str(tmp_path))


def test_no_file_means_no_curation(tmp_path):
    assert curation.load(str(tmp_path)).apply(RECORD, KLASS) == KLASS


def test_a_pin_overrides_named_fields_including_lowering_sensitivity(tmp_path):
    curated = write(tmp_path, {'pin': {'alice/tool': {'sensitivity': 'none', 'domain': 'identity-lookup'}}})

    result = curated.apply(RECORD, KLASS)

    assert (result['sensitivity'], result['domain'], result['scope'], result['source']) == ('none', 'identity-lookup', 'core', 'curation')


def test_pins_match_current_and_former_names_and_ids_case_insensitively(tmp_path):
    for key in ('ALICE/TOOL', 'alice/old-name', '77'):
        curated = write(tmp_path, {'pin': {key: {'form': 'library'}}})
        assert curated.apply(RECORD, KLASS)['form'] == 'library', key


def test_a_blocked_repository_is_out_of_scope_with_no_domain(tmp_path):
    result = write(tmp_path, {'block': ['alice/tool']}).apply(RECORD, KLASS)

    assert (result['scope'], result['domain'], result['domain_secondary']) == ('out', None, None)


def test_unrelated_repositories_are_untouched(tmp_path):
    other = {'id': 1, 'full_name': 'bob/else', 'names': []}

    assert write(tmp_path, {'pin': {'alice/tool': {'form': 'library'}}, 'block': ['carol/x']}).apply(other, KLASS) == KLASS


@pytest.mark.parametrize('content', [
    {'pins': {}},                                          # unknown top-level key
    {'pin': {'a/b': {'scope': 'maybe'}}},                  # value outside the vocabulary
    {'pin': {'a/b': {'colour': 'red'}}},                   # unknown field
    {'pin': {'a/b': {}}},                                  # empty pin
    {'block': 'a/b'},                                      # not a list
    ['a/b'],                                               # not an object
])
def test_a_malformed_file_raises_instead_of_being_ignored(tmp_path, content):
    (tmp_path / 'curation.json').write_text(json.dumps(content), encoding='utf-8')

    with pytest.raises(ValueError):
        curation.load(str(tmp_path))


def test_invalid_json_raises(tmp_path):
    (tmp_path / 'curation.json').write_text('{"pin": ', encoding='utf-8')

    with pytest.raises(ValueError):
        curation.load(str(tmp_path))
