from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, NewType, Optional, Set

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from .config_protocol import ConfigConvertible
from .report_protocol import ReportConvertible


class SafetyBaseModel(ReportConvertible):
    pass


class SafetyConfigBaseModel(ConfigConvertible):
    pass


class ReportSchemaVersion(Enum):
    v3_0 = "3.0"


class PolicyConfigSchemaVersion(Enum):
    v3_0 = "3.0"


class VulnerabilitySeverityLabels(Enum):
    UNKNOWN = "unknown"
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EPSSExploitabilityLabels(Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IgnoreCodes(Enum):
    unpinned_specification = "unpinned-specification"
    environment_dependency = "environment-dependency"
    cvss_severity = "cvss-severity"
    manual = "manual"


@dataclass
class IgnoredItemDetail:
    code: IgnoreCodes = IgnoreCodes.manual
    reason: Optional[str] = None
    expires: Optional[date] = None
    specifications: Optional[Set[Any]] = None


IgnoredItems = NewType("IgnoredItems", Dict[str, IgnoredItemDetail])


class ScanType(Enum):
    scan = "scan"
    system_scan = "system-scan"
    check = "check"

    @classmethod
    def from_command(cls, command):
        return {"project": cls.scan, "system": cls.system_scan, "check": cls.check}.get(
            command.name, None
        )


class Stage(str, Enum):
    development = "development"
    cicd = "cicd"
    production = "production"


STAGE_ID_MAPPING = MappingProxyType(
    {Stage.development: 1, Stage.cicd: 2, Stage.production: 3}
)


class AuthenticationType(str, Enum):
    TOKEN = "token"
    API_KEY = "api_key"
    NONE = "unauthenticated"

    def is_allowed_in(self, stage: Stage = Stage.development) -> bool:
        if self is AuthenticationType.NONE:
            return False

        if self is AuthenticationType.API_KEY and stage is Stage.development:
            return False

        if self is AuthenticationType.TOKEN and stage is not Stage.development:
            return False

        return True


class FileType(Enum):
    REQUIREMENTS_TXT = "requirements.txt"
    POETRY_LOCK = "poetry.lock"
    PIPENV_LOCK = "Pipfile.lock"
    SAFETY_PROJECT = ".safety-project.ini"
    VIRTUAL_ENVIRONMENT = "pyvenv.cfg"
    PYPROJECT_TOML = "pyproject.toml"

    @property
    def ecosystem(self):
        if self in (
            FileType.REQUIREMENTS_TXT,
            FileType.POETRY_LOCK,
            FileType.PIPENV_LOCK,
            FileType.VIRTUAL_ENVIRONMENT,
            FileType.PYPROJECT_TOML,
        ):
            return Ecosystem.PYTHON
        if self is FileType.SAFETY_PROJECT:
            return Ecosystem.SAFETY_PROJECT

        return Ecosystem.UNKNOWN

    def human_name(self, plural: bool = False):
        if self is FileType.POETRY_LOCK:
            return "Python poetry lock files" if plural else "Python poetry lock file"

        if self is FileType.PIPENV_LOCK:
            return "Python Pipfile lock files" if plural else "Python Pipfile lock file"

        if self is FileType.REQUIREMENTS_TXT:
            return "Python requirements files" if plural else "Python requirement file"

        if self is FileType.VIRTUAL_ENVIRONMENT:
            return "Python environments" if plural else "Python environment"

        if self is FileType.SAFETY_PROJECT:
            return "Safety projects" if plural else "Safety project"

        if self is FileType.PYPROJECT_TOML:
            return "Python pyproject.toml files" if plural else "Python pyproject.toml file"


class Ecosystem(Enum):
    PYTHON = "python"
    SAFETY_PROJECT = "safety_project"
    UNKNOWN = "unknown"

    @property
    def file_types(self) -> List[FileType]:
        if self is Ecosystem.PYTHON:
            return [
                FileType.REQUIREMENTS_TXT,
                FileType.POETRY_LOCK,
                FileType.PIPENV_LOCK,
                FileType.VIRTUAL_ENVIRONMENT,
                FileType.PYPROJECT_TOML,
            ]
        if self is Ecosystem.SAFETY_PROJECT:
            return [FileType.SAFETY_PROJECT]

        return []

    @classmethod
    def scannable(cls) -> List["Ecosystem"]:
        return [cls.PYTHON]


class PolicySource(Enum):
    local = "local"
    cloud = "cloud"


class InstallationAction(Enum):
    allow = "allow"
    deny = "deny"


class PackageEcosystem(Enum):
    pip = "pip"
