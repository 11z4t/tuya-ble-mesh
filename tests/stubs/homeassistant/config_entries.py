"""Minimal stub for homeassistant.config_entries."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

HANDLERS: dict[str, Any] = {}

_DataT = TypeVar("_DataT")


class ConfigEntry(Generic[_DataT]):
    entry_id: str
    title: str
    data: dict[str, Any]
    options: dict[str, Any]
    unique_id: str | None
    runtime_data: Any

    def __init__(
        self,
        *,
        entry_id: str = "test_entry",
        title: str = "",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        unique_id: str | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.title = title
        self.data = data or {}
        self.options = options or {}
        self.unique_id = unique_id


class OptionsFlow:
    """Minimal stub for HA OptionsFlow."""

    hass: Any = None
    context: dict[str, Any]

    def __init__(self) -> None:
        self.context = {}

    @property
    def show_advanced_options(self) -> bool:
        """Return True if the user enabled advanced options in HA settings."""
        return self.context.get("show_advanced_options", False)

    def async_abort(self, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_create_entry(self, *, title: str = "", data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

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


class _ConfigFlowMeta(type):
    """Metaclass that accepts domain= keyword in class definition.

    Mirrors how HA's real FlowHandler metaclass processes the ``domain=``
    keyword argument so that ``class MyFlow(ConfigFlow, domain=DOMAIN)``
    works in the test stub environment.
    """

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        domain: str | None = None,
        **kwargs: Any,
    ) -> _ConfigFlowMeta:
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if domain is not None:
            # Register the flow handler by domain (mirrors HANDLERS dict in real HA)
            HANDLERS[domain] = cls
        return cls

    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if domain is not None:
            HANDLERS[domain] = cls


class ConfigFlow(metaclass=_ConfigFlowMeta):
    VERSION = 1
    _disc_info: Any = None
    # Unique ID set by async_set_unique_id(); checked by _abort_if_unique_id_configured()
    _unique_id: str | None = None
    # Mock hass for tests that need entry lookup
    hass: Any = None
    context: dict[str, Any]

    def __init__(self) -> None:
        self.context = {}

    @property
    def show_advanced_options(self) -> bool:
        """Return True if the user enabled advanced options in HA settings."""
        return self.context.get("show_advanced_options", False)

    async def async_set_unique_id(self, unique_id: str, *, raise_on_progress: bool = True) -> None:
        """Store unique_id for later duplicate checking."""
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self, updates: dict[str, Any] | None = None) -> None:
        """No-op in stub — no config entries to check against in unit tests."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        return {}

    def async_abort(self, reason: str) -> dict[str, Any]:
        return {"type": "abort", "reason": reason}

    def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": "create_entry", "title": title, "data": data}

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

    def async_update_reload_and_abort(
        self,
        entry: Any,
        *,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        reason: str = "reconfigure_successful",
        reload_even_if_entry_is_unchanged: bool = True,
    ) -> dict[str, Any]:
        """Stub: update entry data and abort with reason."""
        if data is not None and self.hass is not None:
            self.hass.config_entries.async_update_entry(entry, data=data)
        return {"type": "abort", "reason": reason}
