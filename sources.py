"""Hugging Face and arXiv as sources next to GitHub: models, datasets, spaces and papers, normalised to `SourceItem`.

Both APIs are public and need no key (an `HF_TOKEN` in the environment is sent to Hugging Face when set, for its higher
rate limit). Like the GitHub client, every call retries rate limits, 5xx and network errors, and raises `SourceError`
once retries are exhausted: a partial result must never be published over a good one.
"""
from __future__ import annotations

import copy
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

HF_API_URL = 'https://huggingface.co/api'
HF_WEB_URL = 'https://huggingface.co'
ARXIV_API_URL = 'https://export.arxiv.org/api/query'
MAX_RETRIES = 3
MAX_WAIT_SECONDS = 120
SUMMARY_CHARS = 400

# Hugging Face list endpoint per kind, with the fields to request (`expand` returns only what is named; `downloads`
# is not defined for spaces, and only datasets carry a description).
HF_KINDS = {
    'model': ('models', '', ['lastModified', 'likes', 'downloads', 'tags', 'pipeline_tag']),
    'dataset': ('datasets', 'datasets/', ['lastModified', 'likes', 'downloads', 'tags', 'description']),
    'space': ('spaces', 'spaces/', ['lastModified', 'likes', 'tags', 'sdk']),
}

SOURCE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    'huggingface': {
        'enabled': True,
        'queries': ['osint', 'prompt injection', 'jailbreak', 'cybersecurity', 'threat intelligence', 'malware'],
        'kinds': ['model', 'dataset', 'space'],
        'min_likes': 5,
        'max_age_days': 365,
        'limit_per_query': 20,      # per query and kind
        'top_n': 10,                # shown in the README
        'api_delay_seconds': 0.5,
    },
    'arxiv': {
        'enabled': True,
        'queries': ['all:"prompt injection"', 'all:OSINT', 'all:"LLM agent" AND all:security',
                    'all:"large language model" AND (all:malware OR all:vulnerability OR all:"threat intelligence")'],
        'categories': ['cs.CR', 'cs.AI', 'cs.CL', 'cs.LG', 'cs.CY'],   # [] = any category
        'max_age_days': 90,
        'limit_per_query': 25,
        'top_n': 10,
        'api_delay_seconds': 3.0,   # arXiv asks for one request every three seconds
    },
}


class SourceError(RuntimeError):
    """Raised when Hugging Face or arXiv keeps failing; a partial result must never be published."""


@dataclass
class SourceItem:
    """One result from a non-GitHub source. All text fields are untrusted and must be escaped where they are shown."""
    source: str                 # 'huggingface' or 'arxiv'
    kind: str                   # 'model', 'dataset', 'space' or 'paper'
    id: str                     # 'owner/name' on Hugging Face, the arXiv id without version
    title: str
    url: str
    summary: str = ''
    date: str = ''              # YYYY-MM-DD: last modified on Hugging Face, first submitted on arXiv
    likes: int = 0
    downloads: int = 0
    tags: List[str] = field(default_factory=list)   # topics, or arXiv categories
    authors: List[str] = field(default_factory=list)


# --- settings ------------------------------------------------------------------------------------------

def merge_source_settings(file_value: Any) -> Dict[str, Dict[str, Any]]:
    """Defaults overlaid with the `sources` section of config.json, per source. Checked strictly, like `llm`: a typo
    here would silently change what is published."""
    settings = copy.deepcopy(SOURCE_DEFAULTS)
    if file_value is None:
        return settings
    if not isinstance(file_value, dict):
        raise ValueError("sources must be an object")
    unknown = set(file_value) - set(settings)
    if unknown:
        raise ValueError(f"Unknown source(s): {', '.join(sorted(unknown))}")
    for name, override in file_value.items():
        if not isinstance(override, dict):
            raise ValueError(f"sources.{name} must be an object")
        bad = set(override) - set(settings[name])
        if bad:
            raise ValueError(f"Unknown sources.{name} setting(s): {', '.join(sorted(bad))}")
        settings[name].update(override)

    for name, section in settings.items():
        for key in ('queries', 'categories', 'kinds'):
            if key in section:
                values = section[key]
                if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                    raise ValueError(f"sources.{name}.{key} must be a list of strings")
                section[key] = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if section['limit_per_query'] < 1 or section['top_n'] < 1 or section['api_delay_seconds'] < 0:
            raise ValueError(f"sources.{name}: limit_per_query and top_n must be at least 1, api_delay_seconds not negative")
        if section['max_age_days'] < 0:
            raise ValueError(f"sources.{name}.max_age_days must not be negative (0 disables the filter)")
    kinds = set(settings['huggingface']['kinds']) - set(HF_KINDS)
    if kinds:
        raise ValueError(f"Unknown sources.huggingface.kinds: {', '.join(sorted(kinds))}")
    if settings['huggingface']['min_likes'] < 0:
        raise ValueError("sources.huggingface.min_likes must not be negative")
    return settings


# --- HTTP ----------------------------------------------------------------------------------------------

def _retry_delay(response: requests.Response, attempt: int) -> Optional[float]:
    """Seconds to wait before retrying a failed response, or None if the failure is not retryable."""
    if response.status_code < 500 and response.status_code != 429:
        return None
    retry_after = response.headers.get('Retry-After')
    wait = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 ** (attempt + 1)
    return wait if wait <= MAX_WAIT_SECONDS else None


def _get(session: requests.Session, url: str, params: Any) -> requests.Response:
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.exceptions.RequestException as e:
            error, delay = f"request failed: {e}", 2.0 ** (attempt + 1)
        else:
            if response.status_code == 200:
                return response
            error, delay = f"status {response.status_code}: {response.text[:200]}", _retry_delay(response, attempt)
        if delay is None or attempt == MAX_RETRIES:
            raise SourceError(f"Request to {url} failed ({error})")
        logger.warning(f"Request to {url} failed ({error}); retrying in {delay:.0f}s ({attempt + 1}/{MAX_RETRIES})")
        time.sleep(delay)
    raise AssertionError('unreachable')


def _cutoff(max_age_days: int) -> Optional[date]:
    return datetime.now(timezone.utc).date() - timedelta(days=max_age_days) if max_age_days > 0 else None


def _clean(text: Any, limit: int) -> str:
    """One line of text: whitespace collapsed, cut to `limit` characters."""
    collapsed = ' '.join(str(text or '').split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit - 1].rstrip() + '…'


def _recent(day: str, cutoff: Optional[date]) -> bool:
    if cutoff is None:
        return True
    try:
        return date.fromisoformat(day) >= cutoff
    except ValueError:
        return False


# --- Hugging Face --------------------------------------------------------------------------------------

def _hf_item(kind: str, raw: Dict[str, Any]) -> Optional[SourceItem]:
    item_id = raw.get('id')
    if not isinstance(item_id, str) or '/' not in item_id and kind != 'model':
        return None
    tags = [t for t in raw.get('tags') or [] if isinstance(t, str) and ':' not in t and t != 'region:us']
    pipeline = raw.get('pipeline_tag')
    if isinstance(pipeline, str) and pipeline not in tags:
        tags.insert(0, pipeline)
    return SourceItem(
        source='huggingface', kind=kind, id=item_id, title=item_id,
        url=f"{HF_WEB_URL}/{HF_KINDS[kind][1]}{item_id}",
        summary=_clean(raw.get('description'), SUMMARY_CHARS),
        date=str(raw.get('lastModified') or '')[:10],
        likes=int(raw.get('likes') or 0), downloads=int(raw.get('downloads') or 0), tags=tags[:10])


def search_huggingface(settings: Dict[str, Any], session: Optional[requests.Session] = None) -> List[SourceItem]:
    """Models, datasets and spaces matching each query, most-liked first, filtered by likes and recency."""
    session = session or requests.Session()
    token = os.getenv('HF_TOKEN')
    if token:
        session.headers['Authorization'] = f"Bearer {token}"
    cutoff = _cutoff(settings['max_age_days'])
    found: Dict[str, SourceItem] = {}
    for query in settings['queries']:
        for kind in settings['kinds']:
            endpoint, _, fields = HF_KINDS[kind]
            params = [('search', query), ('sort', 'likes'), ('direction', '-1'), ('limit', settings['limit_per_query'])]
            params += [('expand[]', field_name) for field_name in fields]
            logger.info(f"Searching Hugging Face {endpoint}: {query}")
            body = _get(session, f"{HF_API_URL}/{endpoint}", params).json()
            if not isinstance(body, list):
                raise SourceError(f"Unexpected Hugging Face response for {endpoint}: {str(body)[:200]}")
            for raw in body:
                item = _hf_item(kind, raw) if isinstance(raw, dict) else None
                if item and item.likes >= settings['min_likes'] and _recent(item.date, cutoff):
                    found.setdefault(item.url, item)
            time.sleep(settings['api_delay_seconds'])
    return sorted(found.values(), key=lambda i: (-i.likes, -i.downloads, i.url))


# --- arXiv ---------------------------------------------------------------------------------------------

_ATOM = '{http://www.w3.org/2005/Atom}'
_ARXIV_ID = re.compile(r'arxiv\.org/abs/(.+?)(?:v\d+)?$')


def _arxiv_query(query: str, categories: List[str]) -> str:
    if not categories:
        return query
    return f"({query}) AND ({' OR '.join(f'cat:{c}' for c in categories)})"


def parse_arxiv(xml_text: str) -> List[SourceItem]:
    """Papers in an arXiv Atom response. An `Error` entry (arXiv reports bad queries as a 200) raises."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError(f"arXiv returned unparseable XML: {e}") from e
    papers = []
    for entry in root.findall(f'{_ATOM}entry'):
        link = (entry.findtext(f'{_ATOM}id') or '').strip()
        if '/api/errors' in link:
            raise SourceError(f"arXiv rejected the query: {_clean(entry.findtext(f'{_ATOM}summary'), 200)}")
        match = _ARXIV_ID.search(link)
        if not match:
            continue
        paper_id = match.group(1)
        papers.append(SourceItem(
            source='arxiv', kind='paper', id=paper_id,
            title=_clean(entry.findtext(f'{_ATOM}title'), 300),
            url=f"https://arxiv.org/abs/{paper_id}",
            summary=_clean(entry.findtext(f'{_ATOM}summary'), SUMMARY_CHARS),
            date=(entry.findtext(f'{_ATOM}published') or '')[:10],
            tags=[c.get('term') for c in entry.findall(f'{_ATOM}category') if c.get('term')][:6],
            authors=[_clean(a.findtext(f'{_ATOM}name'), 80) for a in entry.findall(f'{_ATOM}author')][:5]))
    return papers


def search_arxiv(settings: Dict[str, Any], session: Optional[requests.Session] = None) -> List[SourceItem]:
    """Papers matching each query within the recency window, newest first."""
    session = session or requests.Session()
    cutoff = _cutoff(settings['max_age_days'])
    found: Dict[str, SourceItem] = {}
    for query in settings['queries']:
        params = {'search_query': _arxiv_query(query, settings['categories']), 'start': 0,
                  'max_results': settings['limit_per_query'], 'sortBy': 'submittedDate', 'sortOrder': 'descending'}
        logger.info(f"Searching arXiv: {params['search_query']}")
        for paper in parse_arxiv(_get(session, ARXIV_API_URL, params).text):
            if _recent(paper.date, cutoff):
                found.setdefault(paper.url, paper)
        time.sleep(settings['api_delay_seconds'])
    return sorted(found.values(), key=lambda p: (p.date, p.url), reverse=True)


def collect_items(settings: Dict[str, Dict[str, Any]]) -> List[SourceItem]:
    """Everything the enabled sources return. Raises SourceError if any enabled source fails."""
    items: List[SourceItem] = []
    if settings['huggingface']['enabled']:
        items += search_huggingface(settings['huggingface'])
    if settings['arxiv']['enabled']:
        items += search_arxiv(settings['arxiv'])
    return items
