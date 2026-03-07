from enum import Enum
from functools import partial
from typing import Any, Optional, Union
from pydantic import BeforeValidator
from typing_extensions import Annotated

from .constants import CLI_SOURCE, GITHUB, ACTION, PYPI, DOCKER, APP


class SourceType(str, Enum):
    """
    Define the source types using URN format for product identification.
    """

    SAFETY_CLI_GITHUB_ACTION = f"{CLI_SOURCE}:{GITHUB}:{ACTION}"
    SAFETY_CLI_PYPI = f"{CLI_SOURCE}:{PYPI}"
    SAFETY_CLI_DOCKER = f"{CLI_SOURCE}:{DOCKER}"
    SAFETY_CLI_GITHUB_APP = f"{CLI_SOURCE}:{GITHUB}:{APP}"

    @property
    def description(self) -> str:
        """
        Return a human-readable description for this source type.
        """
        descriptions = {
            self.SAFETY_CLI_GITHUB_ACTION: "Safety CLI via GitHub Action",
            self.SAFETY_CLI_PYPI: "Safety CLI via Python Package Index (PyPI)",
            self.SAFETY_CLI_DOCKER: "Safety CLI via Docker",
            self.SAFETY_CLI_GITHUB_APP: "Safety CLI via GitHub App",
        }
        return descriptions[self]

    @classmethod
    def choices(cls):
        """
        Return this Enum as choices format (value, display_name).
        """
        return [(item.value, item.description) for item in cls]


class EventTypeBase(str, Enum):
    """
    Base class for all event types
    """

    pass


class EventType(EventTypeBase):
    """
    Enumeration for different types of events.
    """

    COMMAND_ERROR = "com.safetycli.command.error"
    COMMAND_EXECUTED = "com.safetycli.command.executed"
    TOOL_COMMAND_EXECUTED = "com.safetycli.tool.command.executed"
    PACKAGE_INSTALLED = "com.safetycli.package.installed"
    PACKAGE_UPDATED = "com.safetycli.package.updated"
    PACKAGE_UNINSTALLED = "com.safetycli.package.uninstalled"
    PACKAGE_BLOCKED = "com.safetycli.package.blocked"
    FIREWALL_HEARTBEAT = "com.safetycli.firewall.heartbeat"
    FIREWALL_CONFIGURED = "com.safetycli.firewall.configured"
    FIREWALL_DISABLED = "com.safetycli.firewall.disabled"

    INIT_STARTED = "com.safetycli.init.started"
    AUTH_STARTED = "com.safetycli.auth.started"
    AUTH_COMPLETED = "com.safetycli.auth.completed"
    FIREWALL_SETUP_RESPONSE_CREATED = "com.safetycli.firewall.setup.response.created"
    FIREWALL_SETUP_COMPLETED = "com.safetycli.firewall.setup.completed"
    CODEBASE_DETECTION_STATUS = "com.safetycli.codebase.detection.status"
    CODEBASE_SETUP_RESPONSE_CREATED = "com.safetycli.codebase.setup.response.created"
    CODEBASE_SETUP_COMPLETED = "com.safetycli.codebase.setup.completed"
    INIT_SCAN_COMPLETED = "com.safetycli.init.scan.completed"
    INIT_EXITED = "com.safetycli.init.exited"

    @property
    def description(self) -> str:
        """
        Return a human-readable description for this event type.
        """
        descriptions = {
            self.COMMAND_ERROR: "Command Error",
            self.COMMAND_EXECUTED: "Command Executed",
            self.TOOL_COMMAND_EXECUTED: "Tool Command Executed",
            self.PACKAGE_INSTALLED: "Package Installed",
            self.PACKAGE_UPDATED: "Package Updated",
            self.PACKAGE_UNINSTALLED: "Package Uninstalled",
            self.PACKAGE_BLOCKED: "Package Blocked",
            self.FIREWALL_HEARTBEAT: "Firewall Heartbeat",
            self.FIREWALL_CONFIGURED: "Firewall Configured",
            self.FIREWALL_DISABLED: "Firewall Disabled",
            self.INIT_STARTED: "Init Started",
            self.AUTH_STARTED: "Auth Started",
            self.AUTH_COMPLETED: "Auth Completed",
            self.FIREWALL_SETUP_RESPONSE_CREATED: "Firewall Setup Response Created",
            self.FIREWALL_SETUP_COMPLETED: "Firewall Setup Completed",
            self.CODEBASE_DETECTION_STATUS: "Codebase Detection Status",
            self.CODEBASE_SETUP_RESPONSE_CREATED: "Codebase Setup Response Created",
            self.CODEBASE_SETUP_COMPLETED: "Codebase Setup Completed",
            self.INIT_SCAN_COMPLETED: "Init Scan Completed",
            self.INIT_EXITED: "Init Exited",
        }
        return descriptions[self]

    @classmethod
    def choices(cls):
        """
        Return this Enum as choices format (value, display_name).
        """
        return [(item.value, item.description) for item in cls]


class ParamSource(str, Enum):
    """
    Matches Click's parameter sources
    """

    COMMANDLINE = "commandline"
    ENVIRONMENT = "environment"
    CONFIG = "config"
    DEFAULT = "default"
    PROMPT = "prompt"

    # Useful for tracking when we couldn't determine the source
    UNKNOWN = "unknown"


class ToolType(str, Enum):
    """
    Supported tools.
    """

    PIP = "pip"
    POETRY = "poetry"
    UV = "uv"
    CONDA = "conda"
    NPM = "npm"


DEFAULT_MAX_BYTES: int = 32 * 1024  # 32 KB
DEFAULT_ENCODING = "utf-8"


def truncate_by_chars(
    value: Union[str, bytes, Any],
    max_chars: int,
    encoding: str = DEFAULT_ENCODING
) -> str:
    """
    Truncates a value to a maximum number of characters.
    """
    # Convert to string if needed
    if isinstance(value, bytes):
        value = value.decode(encoding, errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    
    return value[:max_chars]


def truncate_by_bytes(
    value: Union[str, bytes, Any],
    max_bytes: int,
    encoding: str = DEFAULT_ENCODING
) -> str:
    """
    Truncates a value to a maximum byte size.
    """
    # Convert to bytes if needed
    if not isinstance(value, bytes):
        value = str(value).encode(encoding, errors="replace")
    
    # Truncate and convert back to string
    return value[:max_bytes].decode(encoding, errors="ignore")


StdOut = Annotated[
    str, BeforeValidator(partial(truncate_by_bytes, max_bytes=DEFAULT_MAX_BYTES))
]
StdErr = Annotated[
    str, BeforeValidator(partial(truncate_by_bytes, max_bytes=DEFAULT_MAX_BYTES))
]
StackTrace = Annotated[
    str, BeforeValidator(partial(truncate_by_bytes, max_bytes=DEFAULT_MAX_BYTES))
]

LimitedStr = Annotated[str, BeforeValidator(partial(truncate_by_chars, max_chars=200))]
