"""Rules-only classification: scope, domain, form, AI role and sensitivity from a repository's name,
description, non-OSINT topics and the head of its README.

Everything here reads untrusted text, so it only counts pattern hits and never follows anything it reads.
Patterns cover English plus the Russian and Chinese vocabulary common in this space; text in other
languages is left unclassified (scope `adjacent`, no domain) for the LLM review stage.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Pattern, Tuple

README_HEAD_CHARS = 3000


def _compile(*patterns: str) -> List[Pattern]:
    return [re.compile(p, re.I) for p in patterns]


# domain -> (scope tier, patterns). Order is the tie-break priority: specific domains come before broad ones.
DOMAINS: Dict[str, Tuple[str, List[Pattern]]] = {
    'identity-lookup': ('core', _compile(
        r'sherlock|maigret|holehe|whatsmyname|ghunt',
        r'user ?name (search|lookup|finder|check|enumerat)|usernames?\b.{0,30}(sites|platforms|sources)',
        r'e-?mail (lookup|osint|finder|address|checker)|phone (number|lookup|osint|check|info)|number (lookup|tracker)',
        r'people search|person search|caller ?id|reverse (phone|email)|full ?name|find (accounts|profiles)',
        r'по (телефону|номеру|нику|email|фио)|пробив|\bфио\b',
        r'поиск по|телефон\w*|\bник\w*\b',
        r'\b(nik|ktp|ssn|aadhaar|curp)\b')),
    'breach-exposure': ('core', _compile(
        r'breach|leak(ed|s)?\b|pwned|\bhibp\b|compromised|data brokers?|credential dump|stealer|infostealer',
        r'exposure|footprint|privacy|opt-?out|removal request|утёк|утек|слив')),
    'crypto-financial': ('core', _compile(
        r'crypto|bitcoin|ethereum|blockchain|wallet|on-?chain|\bofac\b|sanction',
        r'\bkyb\b|due diligence|shell compan|beneficial owner|company (registry|records)|\begrul\b|ЕГРЮЛ|financial investig|fraud')),
    'media-forensics': ('core', _compile(
        r'forensic|metadata|provenance|fact-?check|deepfake|exiftool|\bexif\b|image (analysis|authenticity)',
        r'(image|video|media|source|claim)s? verification|verify (images?|videos?|claims?)|document discovery')),
    'disinformation': ('core', _compile(
        r'disinformation|misinformation|propaganda|influence operation|\bfimi\b|coordinated (inauthentic|amplification)',
        r'narratives?\b|troll|astroturf|rumou?rs?\b|debunk')),
    'dark-web': ('core', _compile(
        r'dark ?web|darknet|\.onion|onion (service|site|link|address)|tor (network|browser|hidden)|\bi2p\b|hidden service')),
    'knowledge': ('core', _compile(
        r'^awesome|awesome\b|curated (list|collection)',
        r'catalogu?e|dossiers?|field guide|\bstandard\b',
        r'research hub|handbook|resources?\b|knowledge base|intel hub')),
    'workspace-analysis': ('core', _compile(
        r'case (management|file|format)|investigation (workspace|platform|board|notebook|tool)|link analysis|maltego',
        r'(knowledge|entity) graph|entity resolution|palantir (alternative|like|clone)|analyst (workbench|workspace)|evidence|timeline',
        r'collaborative.{0,20}(investigation|osint)|organi[sz]ing.{0,20}investigation')),
    'web-capture': ('core', _compile(
        r'scrap(er|ing|e)\b|crawl(er|ing)?|wayback|common ?crawl|web capture|web archiv|\bspider\b',
        r'dork(ing|s)?\b|open-web|yt-dlp|archive\.(org|today)')),
    'transport-sensor': ('core', _compile(
        r'ads-?b|aircraft|flight|vessel|maritime|\bais\b|ship tracking|\bsdr\b|radio|spectrum|\brf\b|\bble\b|wi-?fi',
        r'railway|plane tracking|alpr|license plate')),
    'geospatial-imagery': ('core', _compile(
        r'geoint|imint|geo-?locat|geospatial|\bgeo\b|satellite|sentinel-?\d|imagery|street ?view',
        r'\bexif\b|\bgps\b|openstreetmap|\bosm\b|photo.{0,20}(taken|location)|reverse image|elevation')),
    'situational-awareness': ('core', _compile(
        r'dashboard|situational|situation (brief|room)|real-?time (monitor|map)|live (map|streams?|feed)|early warning',
        r'conflict|\bwar\b|crisis|events? map|threat map|news (monitor|workspace)|geopolitic')),
    'social-media': ('core', _compile(
        r'instagram|tiktok|telegram|twitter|\bx\.com\b|facebook|linkedin|reddit|discord|vkontakte|\bvk\b|snapchat',
        r'\bthreads\b|mastodon|bluesky|youtube|social ?media|socmint|followers|following')),
    'infrastructure-asm': ('adjacent', _compile(
        r'subdomain|\bdns\b|whois|shodan|censys|zoomeye|certificate|\bssl\b|\basn\b|\bbgp\b|\bptr\b',
        r'ip (address|intelligence|lookup|usage)|attack surface|\basm\b|\beasm\b|\bctem\b|passive recon',
        r'port scan|fingerprint|tech(nology)? (stack|detect)|hostname|\bcidr\b|domain (enumeration|intel)|asset discovery',
        r'暴露面|资产|子域名|指纹')),
    'threat-intel': ('adjacent', _compile(
        r'threat intel(ligence)?|\bcti\b|\bioc\b|indicators? of compromise|mitre|att&ck|\bapt\b',
        r'malware|virustotal|\bmisp\b|\bstix\b|threat hunting')),
}

# These carry no OSINT signal unless the name/description itself says OSINT.
_GATED_DOMAINS = {'knowledge'}
_OSINT_TERM = re.compile(r'\bosint\b|open[- ]source intelligence|\bgeoint\b|\bsocmint\b|\bimint\b|\badint\b', re.I)
_OSINT_TOPIC = re.compile(r'osint|open-source-intelligence', re.I)

# Offensive tooling, training material and unrelated scrapers that carry an OSINT tag.
OUT_PATTERNS = _compile(
    r'exploit|payload|reverse shell|ransomware|keylogger|\brat\b|c2 (server|framework|infrastructure)|command and control|rootkit',
    r'phishing|vishing|smishing|social[- ]engineering (lab|drill|campaign|engagement|planning)|pretext',
    r'brute-?force|password (crack|list|generator|spray)|wordlist|\bddos\b|credential (stuffing|checker)',
    r'pen-?test|penetration[- ]test|red[- ]team|bug[- ]?bounty|\bctf\b|hacking (roadmap|course|guide)|roadmap',
    r'vulnerability (scanner|correlation)|admin panel|login scanner|nmap|fuzz(ing|er)|\brfid\b|\bnfc\b|flipper',
    r'study notes|curriculum|\bcourse\b|\blab\b|(phishing|attack|vishing|drill|breach) simulator|awareness (lab|training)|training|drill|\bbootcamp\b',
    r'lead (generation|gen)|business (data|leads)|google maps scraper|proxy (scraper|checker|list)|proxies|openbullet',
    r'cookie (checker|& session)|session checker|trading|bookmarks|offensive (security|tools)')

_FORM_RULES: List[Tuple[str, Pattern]] = [
    ('document', re.compile(r'^awesome|awesome[- ]|curated|catalogu?e|dossiers?|bookmarks|handbook|roadmap|field guide|\bstandard\b|cheat ?sheet', re.I)),
    ('mcp-server', re.compile(r'\bmcp\b.{0,30}server|server.{0,20}\bmcp\b|model context protocol', re.I)),
    ('agent-skill', re.compile(r'agent skills?|\bskills?\b.{0,30}(claude|cursor|codex|agents?)|(claude|cursor|codex).{0,30}\bskills?\b', re.I)),
    ('bot', re.compile(r'(telegram|discord|slack) bot|\bbot\b', re.I)),
    ('browser-extension', re.compile(r'browser extension|chrome extension|firefox (add-?on|extension)|userscript', re.I)),
    ('library', re.compile(r'\b(python|node|go|rust) (library|package|module)\b|\bsdk\b|\blibrary\b', re.I)),
    ('desktop-app', re.compile(r'desktop (app|application|workspace)|electron|tauri|pyqt|windows app', re.I)),
    ('web-app', re.compile(r'dashboard|web ?ui|web app|self-hosted|flask|fastapi|next\.?js|streamlit|\bgui\b|platform', re.I)),
    ('cli', re.compile(r'\bcli\b|command[- ]line|terminal|toolkit|\btool\b|script', re.I)),
]

_AI_TERMS = re.compile(r'\bllms?\b|\bgpt|claude|openai|gemini|ollama|\bai\b|ai-(powered|assisted|driven)|machine learning|\brag\b', re.I)
_AGENT_TERMS = re.compile(r'\bagent(ic|s)?\b|autonomous|multi-agent|swarm', re.I)
_SKILL_MCP = re.compile(r'\bmcp\b|agent skills?|model context protocol', re.I)

# (tag, pattern). A "high" tag keeps a repository off the public boards entirely.
_HIGH_SENSITIVITY: List[Tuple[str, Pattern]] = [
    ('probiv', re.compile(r'пробив|probiv|probit', re.I)),
    ('private-account-inference', re.compile(
        r'(private|hidden)\W{1,4}((private|hidden)\W{1,4})?(instagram |tiktok |telegram )?(account|profile)s?'
        r'|закрыт\w+ (аккаунт|профиль)', re.I)),
    ('national-id-lookup', re.compile(
        r'\b(nik|ktp|ssn|aadhaar|curp)\b.{0,60}(lookup|check|valid|track|pelacakan|cek|info)'
        r'|(lookup|check|track|pelacakan|cek).{0,40}\b(nik|ktp|ssn|aadhaar|curp)\b', re.I)),
    ('leaked-data-lookup', re.compile(
        r'(leaked|breached|утёк\w*|утек\w*|слив\w*).{0,30}(database|records|dumps?|баз\w*|записи|запис\w*)', re.I)),
    ('doxxing', re.compile(r'\bdoxx?(ing)?\b|deanonymi[sz]', re.I)),
    ('face-recognition-people', re.compile(r'face (search|recognition|match(ing)?|finder)|facial recognition|распознавани\w+ лиц', re.I)),
]
_CAUTION_SENSITIVITY: List[Tuple[str, Pattern]] = [
    ('personal-data-lookup', re.compile(
        r'phone (number|lookup|osint|check|info|tracker)|number (lookup|tracker)|e-?mail (lookup|osint|finder|checker)'
        r'|user ?name (search|lookup|finder)|people search|caller ?id|reverse (phone|email)|по (телефону|номеру|нику|email)'
        r'|\b(for|by|across)\s+(user ?names?|e-?mails?|phone numbers?)\b', re.I)),
    ('social-profile-extraction', re.compile(
        r'(instagram|tiktok|facebook|linkedin|telegram|threads|twitter).{0,40}(osint|scrap|extract|profile|investigat|stalk)'
        r'|(osint|scrap|extract).{0,40}(instagram|tiktok|facebook|linkedin|telegram|threads)', re.I)),
    ('person-tracking', re.compile(r'track(ing)? (a )?(person|someone|people|individual|target)|stalk|covert', re.I)),
    ('breach-lookup', re.compile(r'breach|\bleak|pwned|compromised|stealer', re.I)),
]

# The closed vocabularies, shared with the LLM review stage and the curation file.
SCOPES = ('core', 'adjacent', 'out')
DOMAIN_NAMES = tuple(DOMAINS) + ('general',)
FORMS = tuple(form for form, _ in _FORM_RULES) + ('unknown',)
AI_ROLES = ('none', 'llm-assisted', 'agentic', 'skill-mcp-native')
SENSITIVITIES = ('none', 'caution', 'high')
SENSITIVITY_TAGS = tuple(tag for tag, _ in _HIGH_SENSITIVITY + _CAUTION_SENSITIVITY)

Classification = Dict[str, object]


def _hits(patterns: List[Pattern], short: str, long_text: str) -> int:
    """Evidence for a pattern group: per pattern, the number of distinct matches (at most 2, so a list of
    brand names is not stronger than a statement), doubled when they are in the name/description and
    counted once when only in the README head."""
    total = 0
    for pattern in patterns:
        found = {m.group(0) for m in pattern.finditer(short)}
        if found:
            total += 2 * min(2, len(found))
        else:
            total += min(2, len({m.group(0) for m in pattern.finditer(long_text)}))
    return total


def _form(text: str, name: str) -> str:
    for form, pattern in _FORM_RULES:
        if pattern.search(name if form == 'document' else text):
            return form
    return 'unknown'


def _ai_role(text: str) -> str:
    if _SKILL_MCP.search(text):
        return 'skill-mcp-native'
    if _AI_TERMS.search(text):
        return 'agentic' if _AGENT_TERMS.search(text) else 'llm-assisted'
    return 'none'


def _sensitivity(text: str) -> Tuple[str, List[str]]:
    high = [tag for tag, pattern in _HIGH_SENSITIVITY if pattern.search(text)]
    if high:
        return 'high', high
    caution = [tag for tag, pattern in _CAUTION_SENSITIVITY if pattern.search(text)]
    return ('caution', caution) if caution else ('none', [])


def classify(name: str, description: Optional[str], topics: List[str], readme_text: Optional[str]) -> Classification:
    """Classify one repository. `name` is the bare repository name."""
    topic_text = ' '.join(t for t in topics if not _OSINT_TOPIC.search(t)).lower()
    short = f"{name} {description or ''}".lower()
    head = (readme_text or '')[:README_HEAD_CHARS].lower()
    full = f"{short} {topic_text} {head}"

    def evidence(patterns: List[Pattern]) -> int:
        # Topics are chosen by the author and often spammed: they add at most one point, so they can
        # confirm a classification but never make one.
        return _hits(patterns, short, head) + (1 if any(p.search(topic_text) for p in patterns) else 0)

    scores = {}
    for domain, (_, patterns) in DOMAINS.items():
        scores[domain] = 0 if domain in _GATED_DOMAINS and not _OSINT_TERM.search(short) else evidence(patterns)
    # README and topics can only reinforce an out-of-scope signal found in the name/description, never create
    # one: legitimate OSINT READMEs routinely mention pentesting, exploits or red teams in passing.
    out_score = _hits(OUT_PATTERNS, short, '') + min(
        1, _hits(OUT_PATTERNS, '', head) + (1 if any(p.search(topic_text) for p in OUT_PATTERNS) else 0))

    core = {d: s for d, s in scores.items() if DOMAINS[d][0] == 'core'}
    adjacent = {d: s for d, s in scores.items() if DOMAINS[d][0] == 'adjacent'}
    best_core, best_adjacent = max(core.values()), max(adjacent.values())

    if out_score >= 2 and out_score >= max(best_core, best_adjacent):
        scope, domain, confidence = 'out', None, min(1.0, out_score / 6)
    elif best_core >= 2 and best_core >= best_adjacent:
        scope = 'core'
        strong = sorted((s for s in scores.values() if s >= 2), reverse=True)
        if len(strong) >= 3 and strong[0] - strong[2] < 3:
            domain, confidence = 'general', 0.3  # a suite spanning several domains has no single one
        else:
            domain = max(core, key=core.get)
            confidence = min(1.0, best_core / 6)
    elif best_adjacent >= 2:
        scope, domain = 'adjacent', max(adjacent, key=adjacent.get)
        confidence = min(1.0, best_adjacent / 6)
    elif _OSINT_TERM.search(short) and out_score < 2:  # one weak hit (e.g. a spammed topic) does not veto an explicit "OSINT"
        scope, domain, confidence = 'core', 'general', 0.3
    else:
        scope, domain, confidence = 'adjacent', None, 0.1

    ranked = sorted(((s, d) for d, s in scores.items() if s >= 2 and d != domain), reverse=True)
    if domain in scores and ranked and scope != 'out':
        confidence *= 1.0 if scores[domain] - ranked[0][0] >= 2 else 0.8
    secondary = ranked[0][1] if ranked and scope != 'out' else None

    sensitivity, tags = _sensitivity(full)
    top = sorted(((s, d) for d, s in scores.items() if s), reverse=True)[:3]
    return {
        'scope': scope,
        'domain': domain,
        'domain_secondary': secondary,
        'form': _form(full, name),
        'ai_role': _ai_role(full),
        'sensitivity': sensitivity,
        'sensitivity_tags': tags,
        'confidence': round(confidence, 2),
        'why': ' '.join([f"{d}={s}" for s, d in top] + ([f"out={out_score}"] if out_score else [])),
    }
