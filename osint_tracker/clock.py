"""GitHub timestamp helpers. GitHub returns ISO-8601 with a trailing 'Z', which Python 3.9 cannot parse natively."""
from __future__ import annotations

from datetime import datetime, timezone

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_ts(value: str) -> datetime:
    """Parse a GitHub timestamp into an aware UTC datetime."""
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    """Format as the second-precision UTC form the GitHub search qualifiers accept."""
    return value.astimezone(timezone.utc).strftime(TS_FORMAT)


def age_days(created_at: str, now: datetime) -> float:
    """Fractional days between a repository's creation and `now`."""
    return (now - parse_ts(created_at)).total_seconds() / 86400
