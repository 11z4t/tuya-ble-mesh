"""Minimal stub for homeassistant.exceptions."""


class HomeAssistantError(Exception):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


class ConfigEntryAuthFailed(HomeAssistantError):
    pass
