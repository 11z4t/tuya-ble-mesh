"""Minimal stub for homeassistant.util.dt."""
from __future__ import annotations
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def now() -> datetime:
    return datetime.now()


def as_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)
