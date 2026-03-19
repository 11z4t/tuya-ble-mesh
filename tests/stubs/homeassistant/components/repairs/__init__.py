"""Minimal stub for homeassistant.components.repairs."""

from __future__ import annotations

from typing import Any


class RepairsFlow:
    context: dict[str, Any]

    def __init__(self) -> None:
        self.context = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        return {}

    def async_create_entry(self, *, title: str = "", data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }
        if description_placeholders is not None:
            result["description_placeholders"] = description_placeholders
        return result
