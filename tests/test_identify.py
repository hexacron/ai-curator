from osint_tracker.identify import find_clones


def record(repo_id, created, full_name=None, description='', readme_sha=None, readme_bytes=0, stars=0):
    return {'id': repo_id, 'created_at': created, 'full_name': full_name or f'owner{repo_id}/tool',
            'description': description, 'stars': [stars],
            'enrich': {'readme': {'sha256': readme_sha, 'bytes': readme_bytes}}}


def test_identical_readmes_mark_the_later_repo_as_a_clone_of_the_earliest():
    records = [record(1, '2026-09-01T00:00:00Z', readme_sha='abc', readme_bytes=4000),
               record(2, '2026-09-05T00:00:00Z', readme_sha='abc', readme_bytes=4000),
               record(3, '2026-09-09T00:00:00Z', readme_sha='abc', readme_bytes=4000)]

    assert find_clones(records) == {2: 1, 3: 1}


def test_short_readmes_are_too_generic_to_prove_a_copy():
    records = [record(1, '2026-09-01T00:00:00Z', readme_sha='abc', readme_bytes=400),
               record(2, '2026-09-05T00:00:00Z', readme_sha='abc', readme_bytes=400)]

    assert find_clones(records) == {}


def test_same_name_and_description_under_another_owner_is_a_clone():
    description = 'Maps Instagram relationships for OSINT investigations'
    records = [record(1, '2026-09-01T00:00:00Z', 'a/instagram-graph', description),
               record(2, '2026-09-20T00:00:00Z', 'b/instagram-graph', description),
               record(3, '2026-09-21T00:00:00Z', 'c/other-name', description)]  # same text, different name

    assert find_clones(records) == {2: 1}


def test_creation_ties_go_to_the_repo_with_more_stars():
    records = [record(1, '2026-09-01T00:00:00Z', readme_sha='abc', readme_bytes=4000, stars=3),
               record(2, '2026-09-01T00:00:00Z', readme_sha='abc', readme_bytes=4000, stars=90)]

    assert find_clones(records) == {1: 2}


def test_a_copy_of_a_copy_resolves_to_the_original():
    records = [record(1, '2026-09-01T00:00:00Z', 'a/tool', 'The very same description text here', readme_sha='x1', readme_bytes=4000),
               record(2, '2026-09-02T00:00:00Z', 'b/tool', 'The very same description text here', readme_sha='x2', readme_bytes=4000),
               record(3, '2026-09-03T00:00:00Z', 'c/other', 'Different words entirely for this one', readme_sha='x2', readme_bytes=4000)]

    assert find_clones(records) == {2: 1, 3: 1}
