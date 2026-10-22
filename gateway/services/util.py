"""Time helpers shared by the whole gateway."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gateway import main as m


def now() -> str:
    return datetime.now(UTC).isoformat()


def as_utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return m.as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.now(UTC)
