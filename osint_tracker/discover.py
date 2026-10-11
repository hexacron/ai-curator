"""Find repositories created inside the window, enumerating past the search API's 1000-result cap."""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta
from typing import Callable, Dict

from github_client import GitHubClient
from osint_tracker.clock import format_ts

logger = logging.getLogger(__name__)

PER_PAGE = 100
MAX_RESULTS = 1000  # the search API never returns more than this many results for one query


def discover(client: GitHubClient, topics: list, start: datetime, end: datetime,
             delay: float, sleep: Callable[[float], None] = time.sleep) -> Dict[int, dict]:
    """Every non-fork, non-archived repository tagged with one of `topics` and created in [start, end].
    Returns raw search items keyed by repository id (a repository matching several topics appears once)."""
    found: Dict[int, dict] = {}
    start, end = start.replace(microsecond=0), end.replace(microsecond=0)
    for topic in topics:
        before = len(found)
        _search_window(client, topic, start, end, delay, sleep, found)
        logger.info(f"topic:{topic}: {len(found) - before} new candidates ({len(found)} total)")
    return found


def _search_page(client: GitHubClient, query: str, page: int, delay: float, sleep: Callable[[float], None]) -> dict:
    """One search request, throttled to the search rate limit. A truncated (incomplete) response is
    retried once, then used as is: the missing repositories only get a gap in today's snapshot."""
    params = {'q': query, 'sort': 'stars', 'order': 'desc', 'per_page': PER_PAGE, 'page': page}
    for attempt in range(2):
        data = client.get_json(f"{client.api_url}/search/repositories", params)
        sleep(delay)
        if not data.get('incomplete_results'):
            return data
        logger.warning(f"Search returned incomplete results (attempt {attempt + 1}/2): {query} page {page}")
    return data


def _search_window(client: GitHubClient, topic: str, start: datetime, end: datetime, delay: float,
                   sleep: Callable[[float], None], found: Dict[int, dict]) -> None:
    """Collect one topic's repositories created in [start, end], bisecting any window above the result cap."""
    query = f"topic:{topic} created:{format_ts(start)}..{format_ts(end)} fork:false archived:false"
    first = _search_page(client, query, 1, delay, sleep)
    total = first['total_count']

    if total > MAX_RESULTS and end > start:
        middle = start + timedelta(seconds=int((end - start).total_seconds() // 2))
        _search_window(client, topic, start, middle, delay, sleep, found)
        _search_window(client, topic, middle + timedelta(seconds=1), end, delay, sleep, found)
        return
    if total > MAX_RESULTS:
        logger.warning(f"{total} repositories tagged {topic} were created in the single second "
                       f"{format_ts(start)}; keeping the {MAX_RESULTS} with the most stars")

    pages = [first]
    for page in range(2, math.ceil(min(total, MAX_RESULTS) / PER_PAGE) + 1):
        pages.append(_search_page(client, query, page, delay, sleep))
    for data in pages:
        for item in data['items']:
            found.setdefault(item['id'], item)
