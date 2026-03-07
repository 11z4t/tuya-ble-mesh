from typing import Any, List, Optional
from pydantic import BaseModel, Field

from ..base import PayloadBase
from ..types import LimitedStr, ParamSource, StackTrace, StdErr, StdOut, ToolType


class CommandParam(BaseModel):
    position: int = Field(description="Position in the original command")
    name: Optional[LimitedStr] = Field(
        default=None, description="Name of the option, None for positional arguments"
    )
    value: Any = Field(description="Value of the argument or option")
    source: ParamSource = Field(
        ParamSource.UNKNOWN,
        description="Source of the parameter value (commandline, environment, config, default, prompt)",
    )

    @property
    def is_option(self) -> bool:
        """
        Return True if this is a named option, False if positional argument
        """
        return self.name is not None


class ProcessStatus(BaseModel):
    stdout: Optional[StdOut] = Field(
        default=None, description="Standard output of the process"
    )
    stderr: Optional[StdErr] = Field(
        default=None, description="Standard error of the process"
    )
    return_code: int = Field(description="Return code of the process")


class CommandExecutedPayload(PayloadBase):
    command_name: str = Field(
        description="Primary command name (e.g., 'status', 'scan')"
    )
    command_path: List[LimitedStr] = Field(
        description="Command path as a list (e.g., ['safety', 'auth', 'login'])"
    )
    raw_command: List[LimitedStr] = Field(
        description="Complete command as a list (equivalent to sys.argv)"
    )
    parameters: List[CommandParam] = Field(
        description="Parameters defined by the us", default_factory=list
    )
    duration_ms: int = Field(
        gt=0,
        description="Execution time in milliseconds for the full command "
        "including any tool call",
    )
    status: ProcessStatus = Field(
        description="Status data (stdout/stderr/return_code) when applicable"
    )


class ToolCommandExecutedPayload(PayloadBase):
    """
    Information about a wrapped command execution.
    """
    tool: ToolType = Field(
        description="Tool Type (e.g., 'pip', 'uv', 'poetry', 'npm')"
    )
    tool_path: Optional[str] = Field(default=None, description="Absolute path to the tool's executable")
    raw_command: List[LimitedStr] = Field(
        description="Complete command as a list (equivalent to sys.argv)"
    )    
    duration_ms: int = Field(
        gt=0,
        description="Execution time in milliseconds",
    )
    status: ProcessStatus = Field(
        description="Status data (stdout/stderr/return_code) when applicable"
    )


class CommandErrorPayload(PayloadBase):
    command_name: Optional[LimitedStr] = Field(
        description="Name of the command that failed"
    )
    command_path: Optional[List[LimitedStr]] = Field(
        description="Command path as a list (e.g., ['safety', 'auth', 'login'])"
    )
    raw_command: List[LimitedStr] = Field(
        description="Complete command as a list (equivalent to sys.argv)"
    )
    error_message: str = Field(description="Error message")
    stacktrace: Optional[StackTrace] = Field(
        default=None, description="Stack trace if available"
    )


class PackagePayloadBase(PayloadBase):
    package_name: str = Field(description="Name of the package")
    tool: ToolType = Field(description="ToolType used (e.g., pip, conda)")
    tool_path: Optional[str] = Field(default=None, description="Absolute path to the tool's executable")
    location: Optional[str] = Field(default=None, description="Location of the package")


class SingleVersionPackagePayload(PackagePayloadBase):
    version: str = Field(description="Version of the package")


class PackageInstalledPayload(SingleVersionPackagePayload):
    pass


class PackageUninstalledPayload(SingleVersionPackagePayload):
    pass


class PackageUpdatedPayload(PackagePayloadBase):
    previous_version: str = Field(description="Previous package version")
    current_version: str = Field(description="Current package version")


class HealthCheckResult(BaseModel):
    """
    Generic health check result structure.
    """

    is_alive: bool = Field(description="Whether the entity is alive and responding")
    response_time_ms: Optional[int] = Field(
        None, description="Response time in milliseconds"
    )
    error_message: Optional[LimitedStr] = Field(
        None, description="Error message if any"
    )
    timestamp: str = Field(description="When the health check was performed")


class IndexConfig(BaseModel):
    """
    Configuration details for the package index.
    """

    is_configured: bool = Field(
        description="Whether the index configuration is in place"
    )
    index_url: Optional[LimitedStr] = Field(
        default=None, description="URL of the configured package index"
    )
    health_check: Optional[HealthCheckResult] = Field(
        default=None, description="Health check for the index"
    )


class AliasConfig(BaseModel):
    """
    Configuration details for the command alias.
    """

    is_configured: bool = Field(description="Whether the alias is configured")
    alias_content: Optional[LimitedStr] = Field(
        default=None, description="Content of the alias"
    )
    health_check: Optional[HealthCheckResult] = Field(
        default=None, description="Health check for the alias"
    )


class ToolStatus(BaseModel):
    """
    Status of a single package manager tool. A single package manager tool is
    being identified by its executable path.
    """

    type: ToolType = Field(description="Tool type")
    command_path: str = Field(description="Absolute path to the tool's executable")
    version: str = Field(description="Version of the tool")
    reachable: bool = Field(
        description="Whether the tool's package manager is reachable bypassing any firewall setup"
    )

    # Configuration information
    alias_config: Optional[AliasConfig] = Field(
        default=None, description="Details about the alias configuration"
    )
    index_config: Optional[IndexConfig] = Field(
        default=None, description="Details about the index configuration"
    )

    @property
    def alias_configured(self) -> bool:
        """
        Whether the alias is configured.
        """
        return self.alias_config is not None and self.alias_config.is_configured

    @property
    def index_configured(self) -> bool:
        """
        Whether the index is configured.
        """
        return self.index_config is not None and self.index_config.is_configured

    @property
    def is_configured(self) -> bool:
        """
        Returns whether the tool is fully configured (both alias and index).
        """
        return self.alias_configured and self.index_configured


class FirewallConfiguredPayload(PayloadBase):
    tools: List[ToolStatus] = Field(
        description="Status of all detected package manager tools"
    )


class FirewallDisabledPayload(PayloadBase):
    reason: Optional[LimitedStr] = Field(
        description="Reason for disabling the firewall"
    )


class FirewallHeartbeatPayload(PayloadBase):
    tools: List[ToolStatus] = Field(
        description="Status of all detected package manager tools"
    )
