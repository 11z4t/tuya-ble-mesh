from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

# from pydantic import BaseModel, Field, root_validator
from typing_extensions import Annotated, Literal

try:
    from pydantic import Field, model_validator  # type: ignore # pragma: no cover
    from pydantic import BaseModel  # type: ignore # pragma: no cover

    MODEL_VALIDATOR_KWARGS = {"mode": "after"}
    LATEST_PYDANTIC = True
except ImportError:
    from pydantic import Field, root_validator as model_validator  # type: ignore # noqa F401 # pragma: no cover
    from pydantic import BaseModel  # type: ignore # pragma: no cover

    MODEL_VALIDATOR_KWARGS = {}
    LATEST_PYDANTIC = False

from .constants import (
    BRANCH_DESC,
    OS_DESCRIPTION_DESC,
    OS_RELEASE_DESC,
    OS_TYPE_DESC,
    PYTHON_VERSION_DESC,
    SAFETY_COMMAND_DESC,
    SAFETY_OPTIONS_DESC,
    SAFETY_SOURCE_DESC,
    SAFETY_VERSION_DESC,
    TAG_DESC,
)


class SchemaModelV30(BaseModel):
    def json(self, *args, **kwargs) -> str:
        if LATEST_PYDANTIC:
            return self.model_dump_json(**kwargs)
        return super().json(**kwargs)

    @classmethod
    def parse_obj(cls: "SchemaModelV30", obj: Any) -> "SchemaModelV30":
        if LATEST_PYDANTIC:
            return cls.model_validate(obj)
        return super(SchemaModelV30, cls).parse_obj(obj)


class Telemetry(SchemaModelV30):
    """
    Telemetry object generated per Safety report; this model holds data related to the
    client application running Safety CLI.
    """

    os_type: Annotated[
        Optional[str], Field(description=OS_TYPE_DESC, examples=["Darwin", "Linux", ""])
    ] = None
    os_release: Annotated[
        Optional[str],
        Field(
            description=OS_RELEASE_DESC,
            examples=["21.4.0", "NT", ""],
        ),
    ] = None
    os_description: Annotated[
        Optional[str],
        Field(
            description=OS_DESCRIPTION_DESC,
            examples=["macOS-12.3.1-arm64-arm-64bit"],
        ),
    ] = None
    python_version: Annotated[
        Optional[str], Field(description=PYTHON_VERSION_DESC, examples=["3.11.1"])
    ] = None
    safety_command: Annotated[
        Optional[str],
        Field(
            description=SAFETY_COMMAND_DESC,
            examples=["check", "configure", "scan"],
        ),
    ] = None
    safety_options: Annotated[
        Dict[str, Dict[str, int]], Field(description=SAFETY_OPTIONS_DESC)
    ]
    safety_version: Annotated[
        str,
        Field(
            ...,
            description=SAFETY_VERSION_DESC,
            examples=["2.4.0b1", "3.0"],
        ),
    ]
    safety_source: Annotated[
        str,
        Field(
            ...,
            description=SAFETY_SOURCE_DESC,
            examples=["cli", "code"],
        ),
    ]


class Git(SchemaModelV30):
    branch: Optional[str] = Field(
        ...,
        description=BRANCH_DESC,
        examples=["feat/find-cve-package-identifiers", None],
        title="Branch",
    )
    tag: Optional[str] = Field(
        ...,
        description=TAG_DESC,
        examples=["2.0.0", None],
        title="Tag",
    )
    commit: Optional[str] = Field(
        ...,
        description="Current git commit of 40 lenght",
        examples=[
            "514235567bf3194a8ae97f8cd1c7f1011bded271",
            "bac8714e03c33e177205fe749e6d994c8ad47634-dirty",
        ],
        title="Commit",
    )
    dirty: bool = Field(
        ...,
        description="If there are modified tracked files "
        "and/or staged changes. Untracked files aren't considered.",
        examples=[True],
        title="Dirty",
    )
    origin: Optional[str] = Field(
        ...,
        description="URL for the origin remote if it exists, otherwise null.",
        examples=["git@github.com:pyupio/pyupio.git", None],
        title="Origin",
    )


class AuthenticationMethod(str, Enum):
    token = "token"
    api_key = "api_key"


class ScanType(str, Enum):
    scan = "scan"
    system_scan = "system-scan"
    check = "check"


class StageType(str, Enum):
    development = "development"
    cicd = "cicd"
    production = "production"


class IgnoredDetails(SchemaModelV30):
    code: str
    reason: Optional[str]
    expires: Optional[str]


class Vulnerability(SchemaModelV30):
    id: Annotated[str, Field()]
    ignored: Annotated[
        Optional[IgnoredDetails],
        Field(..., description="Only exported when the vulnerability is " "ignored"),
    ] = None
    vulnerable_spec: Annotated[str, Field()]

    def __get_exclude_fields__(self, kwargs):
        exclude = kwargs.pop("exclude", set())
        if not exclude:
            exclude = set()

        optional_properties = ["ignored", "analyzed_version"]

        for opt_property in optional_properties:
            value = None
            try:
                value = getattr(self, opt_property)
            except AttributeError:
                pass

            if not value:
                exclude.add(opt_property)
        return exclude

    def dict(self, **kwargs):
        exclude = self.__get_exclude_fields__(kwargs)
        if LATEST_PYDANTIC:
            return self.model_dump(exclude=exclude, **kwargs)
        return super().dict(exclude=exclude, **kwargs)

    def json(self, **kwargs) -> str:
        exclude = self.__get_exclude_fields__(kwargs)
        if LATEST_PYDANTIC:
            return self.model_dump_json(exclude=exclude, **kwargs)
        return super().json(exclude=exclude, **kwargs)


if LATEST_PYDANTIC:
    Vulnerability.model_rebuild()
else:
    Vulnerability.update_forward_refs()


class ClosestSecureSpecification(SchemaModelV30):
    upper: Optional[str]
    lower: Optional[str]


class Remediation(BaseModel):
    vulnerabilities_found: int
    closest_secure: Annotated[
        Optional[ClosestSecureSpecification], Field(alias="closest_secure_version")
    ] = None
    recommended: Optional[str] = None
    other_recommended: List[str] = []


class SpecificationVulnerabilities(BaseModel):
    known_vulnerabilities: List[Vulnerability]
    remediation: Optional[Remediation] = None


class AnalyzedSpecification(BaseModel):
    raw: str
    vulnerabilities: SpecificationVulnerabilities


class Package(BaseModel):
    name: str
    specifications: List[AnalyzedSpecification]


class FullPackage(Package):
    found: str


class Results(BaseModel):
    dependencies: List[Union[Package, FullPackage]] = []


class File(SchemaModelV30):
    location: Annotated[
        str,
        Field(
            ...,
            description="The full path of the file in the host machine.",
            examples=["/Development/pyupio"],
            title="Location",
        ),
    ]
    type: Annotated[str, Field(..., examples=["pyproject.toml"], title="Type")]
    categories: Annotated[
        List[str], Field(..., examples=["python"], title="Categories")
    ]
    results: Annotated[Results, Field(..., title="Results")]


class PolicySource(Enum):
    local = "local"
    cloud = "cloud"


class Meta(SchemaModelV30):
    scan_type: Annotated[ScanType, Field()]
    stage: Annotated[StageType, Field()] = StageType.development
    scan_locations: Annotated[
        List[str],
        Field(
            ...,
            description="Absolute path from where the scan was ran.",
            examples=["/full-path"],
        ),
    ]
    authenticated: Annotated[
        bool,
        Field(
            ...,
            description="Indicate whether the scan report was ran by an "
            "authenticated session. This is true if any "
            "authentication method like token or api_key was used.",
            examples=[True, False],
        ),
    ]
    authentication_method: Annotated[Optional[AuthenticationMethod], Field()]
    timestamp: Annotated[
        datetime,
        Field(..., examples=["2023-03-28T15:30:24"], title="Scan report generation."),
    ]
    telemetry: Annotated[Telemetry, Field(..., title="Telemetry")]
    schema_version: Annotated[Literal["3.0"], Field(...)] = "3.0"


class Policy(SchemaModelV30):
    id: str
    path: Optional[str] = Field(
        ...,
        description="Relative path to the policy file used taking as a "
        "reference the location property. It's null if the source is cloud.",
        examples=[
            ".safety-policy.yml",
            "../../upper_leve/.safety-general-policy.yml",
            None,
        ],
        title="Policy path",
    )
    source: PolicySource


class Projects(SchemaModelV30):
    id: str = Field(
        ...,
        description="This project id is tied and validated against "
        "the Safety platform.",
        title="Id",
    )
    upload_request_id: str


class ProjectsScan(SchemaModelV30):
    id: str = Field(
        ...,
        description="This project id is tied and validated "
        "against the Safety platform.",
        title="Id",
    )
    upload_request_id: Optional[str] = None
    location: str = Field(
        ...,
        description="Absolute path to the project root.",
        examples=["/path-to-project-root"],
        title="Location",
    )
    policy: Optional[Policy] = Field(
        ...,
        description="Describe the policy used for this scan "
        "in this particular project.",
        title="Policy",
    )
    git: Optional[Git] = Field(..., title="Git")
    files: List[File] = Field(..., title="Files")


class ScanResults(BaseModel):
    files: Annotated[
        List[File],
        Field(
            ...,
            description="Array of files found in an environment "
            "scan with at least one finding of any type.",
            title="Files",
        ),
    ] = []
    projects: Annotated[
        Union[List[Projects], List[ProjectsScan]],
        Field(
            ...,
            description="This list all the projects found with "
            "its respective files or env results.",
            title="Projects",
        ),
    ] = []


class Report(SchemaModelV30):
    meta: Annotated[
        Meta,
        Field(
            ...,
            description="Represents general data about the scan "
            "executed in the target host.",
            examples=["TODO, PENDING to add examples"],
            title="Metadata",
        ),
    ]
    scan_results: Annotated[ScanResults, Field(..., title="Scan Results")]

    if LATEST_PYDANTIC:

        @model_validator(mode="after")
        def validate_projects(self):
            return self._validate_projects(self)

    else:

        @classmethod
        @model_validator(pre=True)
        def validate_projects(cls, values):
            return cls._validate_projects(cls(**values))

    @staticmethod
    def _validate_projects(report):
        try:
            scan_type = report.meta.scan_type
        except Exception:
            raise ValueError("Unable to parse meta from the file report.")

        try:
            projects = report.scan_results.projects
        except Exception:
            raise ValueError("Unable to parse projects from the file report.")

        if scan_type is ScanType.scan and any(
            isinstance(p, Projects) for p in projects
        ):
            raise ValueError(
                "If scan_type is 'scan', only ProjectsScan objects "
                "should be used in results.projects."
            )

        if scan_type is ScanType.system_scan and any(
            isinstance(p, ProjectsScan) for p in projects
        ):
            raise ValueError(
                "If scan_type is not 'scan', only Projects objects "
                "should be used in results.projects."
            )

        return report
