from pydantic import BaseModel, Field
from typing import Optional, List

from .types import SourceType

from typing_extensions import Annotated
from pydantic.types import StringConstraints

class ClientInfo(BaseModel):
    """
    Information about the client application.
    """

    identifier: SourceType = Field(description="Client source identifier name")
    version: str = Field(description="Client application version")
    path: str = Field(description="Path to the client executable")


class ProjectInfo(BaseModel):
    """
    Information about the project context.
    """

    id: str = Field(default="unknown", description="Project identifier")
    url: Optional[str] = Field(default=None, description="Project URL")


class UserInfo(BaseModel):
    """
    Information about the user.
    """

    name: str = Field(description="Username")
    home_dir: str = Field(description="User's home directory")


class OsInfo(BaseModel):
    """
    Information about the operating system.
    """
    architecture: Annotated[str, StringConstraints(to_lower=True)] = Field(description="Machine architecture")
    platform: Annotated[str, StringConstraints(to_lower=True)] = Field(description="Operating system platform")
    name: Annotated[Optional[str], StringConstraints(to_lower=True)] = Field(description="Operating system name")
    version: Annotated[Optional[str], StringConstraints(to_lower=True)] = Field(description="Operating system version")
    kernel_version: Annotated[Optional[str], StringConstraints(to_lower=True)] = Field(
        default=None, description="Kernel version if available"
    )


class HostInfo(BaseModel):
    """
    Information about the host machine.
    """

    name: str = Field(description="Hostname")
    ipv4: Optional[str] = Field(default=None, description="IPv4 address")
    ipv6: Optional[str] = Field(default=None, description="IPv6 address")
    timezone: Optional[str] = Field(default=None, description="Timezone")


class PythonInfo(BaseModel):
    """
    Detailed information about the Python environment.
    """

    version: str = Field(description="Python version (major.minor)")
    path: str = Field(description="Path to the Python executable")
    sys_path: List[str] = Field(description="Python sys.path")
    implementation: Optional[str] = Field(
        default=None, description="Python implementation (e.g., 'CPython')"
    )
    implementation_version: Optional[str] = Field(
        default=None, description="Python implementation version"
    )

    sys_prefix: str = Field(description="sys.prefix location")
    site_packages: List[str] = Field(description="List of site-packages directories")
    user_site_enabled: bool = Field(
        description="Whether user site-packages are enabled for imports"
    )
    user_site_packages: Optional[str] = Field(
        default=None, description="User site-packages directory path if available"
    )

    encoding: str = Field(description="Default string encoding")
    filesystem_encoding: str = Field(description="Filesystem encoding")


class RuntimeInfo(BaseModel):
    """
    Information about the runtime environment.
    """

    workdir: str = Field(description="Working directory")
    user: UserInfo = Field(description="User information")
    os: OsInfo = Field(description="Operating system information")
    host: HostInfo = Field(description="Host information")
    python: Optional[PythonInfo] = Field(default=None, description="Python information")


class EventContext(BaseModel):
    """
    Complete context information for an event.
    Contains details about the client, project, and runtime environment.
    """

    client: ClientInfo = Field(description="Client application information")
    runtime: RuntimeInfo = Field(description="Runtime environment information")
    project: Optional[ProjectInfo] = Field(
        default=None, description="Project information"
    )
    tags: Optional[List[str]] = Field(
        default=None, description="Event tags for categorization"
    )
