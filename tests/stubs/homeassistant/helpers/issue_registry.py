"""Minimal stub for homeassistant.helpers.issue_registry."""
from __future__ import annotations
from typing import Any


class IssueSeverity:
    ERROR = "error"
    WARNING = "warning"


def async_create_issue(
    hass: Any,
    domain: str,
    issue_id: str,
    *,
    is_fixable: bool = False,
    severity: str = IssueSeverity.WARNING,
    translation_key: str = "",
    translation_placeholders: dict[str, str] | None = None,
) -> None:
    pass


def async_delete_issue(hass: Any, domain: str, issue_id: str) -> None:
    pass
