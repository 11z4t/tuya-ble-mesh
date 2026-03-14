"""Minimal stub for homeassistant.util.dt."""
from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def now() -> datetime:
    return datetime.now()


def as_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
