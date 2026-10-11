"""Regression fixture for the rules classifier: real repository texts (tests/gold_repos.json) with labels
written by hand from reading them. It guards the rules against drift; it is not an accuracy measurement,
because the rules were tuned while looking at these same repositories."""
import json
from pathlib import Path

import pytest

from osint_tracker.classify import classify

GOLD = {r['full_name'].lower(): r for r in json.loads((Path(__file__).parent / 'gold_repos.json').read_text(encoding='utf-8'))}

CORE, ADJ, OUT = {'core'}, {'adjacent'}, {'out'}
# full_name -> (allowed scopes, allowed domains or None, sensitivity or None)
EXPECTED = {
    'oldcircle/geo-sleuth': (CORE, {'geospatial-imagery'}, None),
    '0x6rss/instagram-private-graph': (CORE, {'social-media'}, 'high'),
    'glaz-boga-poisk-bot/glaz-boga': (CORE, {'identity-lookup'}, 'high'),
    'socai-io/jev-social': (CORE, {'social-media'}, None),
    'probit-vk/probiv-vk': (CORE, {'identity-lookup', 'social-media'}, 'high'),
    'm4elstr0m/gophoner': (CORE, {'identity-lookup'}, 'caution'),
    '4x3/nulltrace': (CORE, {'breach-exposure'}, 'none'),
    'osintshifu/awesome-osint-repos': (CORE, {'knowledge'}, 'none'),
    'useosint/skills': (CORE, {'general'}, None),
    'anuragroy485/telegram-osint-toolkit': (CORE, {'social-media'}, 'caution'),
    'yingfff123/atlasx': (ADJ, {'infrastructure-asm'}, None),
    'boty-po-probivu/probiv-informacii': (CORE, {'identity-lookup'}, 'high'),
    'devcop95/shodan_reconsx': (ADJ, {'infrastructure-asm'}, None),
    'fadehack/strypt': (CORE, {'media-forensics'}, None),
    'wongedyan/ckelepel-threads': (CORE, {'social-media'}, None),
    'gacut/opentrace': (CORE, {'workspace-analysis'}, None),
    # three single-hit domains tie; the rules call that a multi-domain suite and leave the call to the LLM stage
    'kaifcodec/phonsint': (CORE, {'identity-lookup', 'general'}, 'caution'),
    'soxoj/claude-osint-deploy': (CORE, {'general'}, None),
    'bobslayerx/crypttrace': (CORE, {'crypto-financial'}, None),
    'drotfix/ghunt-panel': (CORE, {'identity-lookup', 'social-media'}, None),
    'ringmast4r/surveillance-industry': (CORE, {'knowledge'}, None),
    'v0l/waveshark': (CORE, {'transport-sensor'}, None),
    'hackunderway/cyberleaks': (CORE, {'breach-exposure'}, 'caution'),
    'ringmast4r/ad-tech-surveillance': (CORE, {'knowledge'}, None),
    'cyruschu430/hk-city-monitor': (CORE, {'situational-awareness'}, None),
    'gorkemguler/metascout': (CORE, {'media-forensics'}, None),
    'jollncoelho/osintgodseye': (CORE, {'transport-sensor', 'situational-awareness', 'general'}, None),
    'billybobmcgee/footprint-osint': (CORE, {'breach-exposure'}, None),
    'amoghbalapurepy-cmd/omen-osint': (CORE, {'breach-exposure'}, None),
    'nagasivagunturu/ioc-insight': (ADJ, {'threat-intel'}, None),
    'adulau/ptrclassify': (ADJ, {'infrastructure-asm'}, None),
    'not-sockpuppet/anakrisis': (CORE, {'general', 'workspace-analysis'}, None),
    'mathdroid/karnak': (CORE, {'situational-awareness'}, None),
    'houseofmvps/opentechalyzer': (ADJ, {'infrastructure-asm'}, None),
    'fadehack/instagram-username-finder': (CORE, {'social-media', 'identity-lookup'}, None),
    'andikaputradev/nik-checker': (CORE, {'identity-lookup'}, 'high'),
    'drafaled/the-trackher': (CORE, {'breach-exposure'}, None),
    'killameep/waypoint-osint': (CORE, {'geospatial-imagery'}, None),
    'truefurina/passive-recon': (ADJ, {'infrastructure-asm'}, None),
    'n11xy/n1xyosint': (CORE, {'identity-lookup'}, 'caution'),
    'osintshifu/filegrail': (CORE, {'media-forensics'}, None),
    'owasp/owasp-open-source-intelligence-standard': (CORE, {'knowledge'}, None),
    'k0nrd/cosint': (CORE, {'workspace-analysis'}, None),
    'afsh4ck/panoptes': (CORE, {'geospatial-imagery'}, None),
    'ail-project/ccwget': (CORE, {'web-capture'}, None),
    'mosimann-adv/tarrafa_scraper': (CORE, {'web-capture'}, None),
    'prettydeath/wallet-attribution': (CORE, {'crypto-financial'}, None),
    'mcasrom/hybrid-fimi-radar': (CORE, {'disinformation'}, None),
    'abognar-git/poisoned-well': (CORE, {'disinformation'}, None),
    '5h4d0wn1k/se2-osint': (CORE, {'general'}, None),
    'ringmast4r/albania-ai-diella': (CORE, {'knowledge', 'general', 'situational-awareness', 'breach-exposure'}, None),
    'ahmet-dedeler/free-north-korea': (CORE, {'knowledge', 'situational-awareness', 'general'}, None),
    '5h4d0wn1k/web5-subdomain': (ADJ, {'infrastructure-asm'}, None),
    'cukierrro/straznik': ({'core', 'adjacent'}, None, None),
    # tagged "osint" but not OSINT tools
    'darama22/malware-research-hub': ({'adjacent', 'out'}, None, None),
    'pazo01/awesome-cyber-ai-arsenal': ({'adjacent', 'out'}, None, None),
    'scottalt/senior-securityengineer-cybersecuritybookmarks': ({'adjacent', 'out'}, None, None),
    'xxlonewolf589xx/snoop-recon-suite': (OUT, None, None),
    'sagarbiswas-multihat/the-blackhat-roadmap': (OUT, None, None),
    'armourinfosec/python-for-security-professionals': (OUT, None, None),
    'krishita17/phantomtap': (OUT, None, None),
    # heavy MITRE/threat-intel vocabulary: the rules call a CVE lab threat-intel-adjacent; the LLM stage decides
    'handnewb/hermes-cybersec-lab': ({'adjacent', 'out'}, None, None),
    'quincunx33/penbox': (OUT, None, None),
    'yaman-redteam/ghostphish': (OUT, None, None),
    'rootbytex/admin-panel-finder': (OUT, None, None),
    'hannesegi/tools-google-maps': (OUT, None, None),
    'zyrexdz/proxy-scraper-checker': (OUT, None, None),
    '0xgetz/0x-cookies': (OUT, None, None),
    '5h4d0wn1k/se1-phishing': (OUT, None, None),
    '5h4d0wn1k/se4-vishing': (OUT, None, None),
    's1n6h/bug-bounty-skills': (OUT, None, None),
    'rootdr-backup/reconner': ({'adjacent', 'out'}, None, None),
}


@pytest.mark.parametrize('full_name', sorted(EXPECTED))
def test_gold_repository(full_name):
    scopes, domains, sensitivity = EXPECTED[full_name]
    repo = GOLD[full_name]

    result = classify(full_name.split('/')[1], repo['description'], repo['topics'], None)

    assert result['scope'] in scopes, result['why']
    if domains is not None:
        assert result['domain'] in domains, result['why']
    if sensitivity is not None:
        assert result['sensitivity'] == sensitivity, result['sensitivity_tags']


def test_every_fixture_has_an_expectation():
    assert set(GOLD) - set(EXPECTED) == set()


def test_text_in_the_readme_cannot_make_an_offensive_toolkit_core():
    result = classify('toolkit', 'Pentesting toolkit: exploit and payload generation', [], 'OSINT ' * 50)

    assert result['scope'] == 'out'


def test_a_suite_spanning_several_domains_is_general_not_an_arbitrary_one():
    description = 'OSINT suite: geolocation of photos, crypto wallet tracing and subdomain enumeration'

    result = classify('suite', description, [], None)

    assert (result['scope'], result['domain']) == ('core', 'general')


def test_author_chosen_topics_alone_do_not_classify_a_repository():
    # a single spammed topic is worth 1 point; a domain needs 2
    result = classify('thing', 'A small tool', ['malware', 'phishing'], None)

    assert result['scope'] != 'core'


def test_passing_readme_mentions_of_pentesting_do_not_reject_an_osint_tool():
    readme = 'Built for pentest and red team work. No exploit code is included, and it never does ddos or brute-force.'

    result = classify('reconlens', 'Passive OSINT toolkit: domain attack surface, username, phone, email', [], readme)

    assert result['scope'] != 'out'


def test_a_subdomain_scanner_is_adjacent_recon_not_out_of_scope():
    result = classify('subsonar', 'Ultra-fast asynchronous subdomain and web-interface scanner with anonymous DNS resolvers',
                      ['bug-bounty'], 'Includes a wordlist and brute-force mode.')

    assert (result['scope'], result['domain']) == ('adjacent', 'infrastructure-asm')


def test_offensive_wording_in_the_description_still_rejects_even_with_osint_in_the_readme():
    result = classify('kit', 'Phishing framework and payload builder', [], 'OSINT OSINT username email phone lookup geolocation')

    assert result['scope'] == 'out'


def test_out_of_scope_wording_in_the_description_loses_to_stronger_recon_evidence():
    description = 'Subdomain, DNS and WHOIS enumeration with port scan; includes a brute-force wordlist'

    result = classify('enumerator', description, [], None)

    assert (result['scope'], result['domain']) == ('adjacent', 'infrastructure-asm'), result['why']
