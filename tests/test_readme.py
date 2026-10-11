import pytest

from osint_tracker.readme import analyze


def checks(text):
    return analyze(text, len(text))['checks']


def test_a_missing_readme_has_no_hash_and_no_sections():
    result = analyze(None)

    assert result['sha256'] is None and result['bytes'] == 0
    assert not any(result['checks'].values())


def test_whitespace_and_case_do_not_change_the_hash_but_content_does():
    original = analyze("# Tool\n\nDoes   a thing.\n")
    reformatted = analyze("# TOOL\nDoes a thing.")
    different = analyze("# Tool\n\nDoes another thing.")

    assert original['sha256'] == reformatted['sha256']
    assert original['sha256'] != different['sha256']


@pytest.mark.parametrize("text, name, expected", [
    ("## Installation\nrun it", 'install', True),
    ("just run `pip install thing` first", 'install', True),
    ("It installs nothing and needs no setup steps", 'install', False),
    ("## Usage\n...", 'usage', True),
    ("```\na\n```\ntext\n```\nb\n```", 'usage', True),  # two fenced blocks
    ("```\na\n```", 'usage', False),
    ("## Disclaimer\nFor authorized use only", 'legal_notice', True),
    ("A fast tool", 'legal_notice', False),
    ("Set your API key in .env", 'api_keys', True),
])
def test_section_checks(text, name, expected):
    assert checks(text)[name] is expected


def test_badges_are_not_screenshots_but_real_images_are():
    badges = "![build](https://img.shields.io/badge/build-passing-green.svg) <img src='https://codecov.io/badge.svg'>"
    screenshot = "![demo](docs/demo.png)"
    html_image = '<img width="800" src="assets/ui.gif">'

    assert checks(badges)['screenshot'] is False
    assert checks(screenshot)['screenshot'] is True
    assert checks(html_image)['screenshot'] is True
