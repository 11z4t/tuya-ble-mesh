from dataclasses import field
from typing import Any, List, Optional

from pydantic.dataclasses import dataclass


@dataclass
class ClosestSecureVersion:
    upper: Optional[str]
    lower: Optional[str]


@dataclass
class RemediationModel:
    vulnerabilities_found: int
    more_info_url: str
    recommended: Optional[str]
    closest_secure: Optional[ClosestSecureVersion] = None
    other_recommended: List[str] = field(default_factory=lambda: [])


@dataclass
class Vulnerability:
    vulnerability_id: str
    package_name: str
    ignored: bool
    vulnerable_spec: Any
    ignored_reason: Optional[str] = None
    ignored_expires: Optional[str] = None
    ignored_code: Optional[str] = None
    all_vulnerable_specs: Optional[List[str]] = None
    analyzed_version: Optional[str] = None
    analyzed_requirement: Optional[str] = None
    advisory: Optional[str] = None
    is_transitive: Optional[bool] = None
    published_date: Optional[str] = None
    fixed_versions: Optional[List[str]] = None
    closest_versions_without_known_vulnerabilities: Optional[List[str]] = None
    resources: Optional[List[str]] = None
    CVE: Optional[Any] = None
    severity: Optional[Any] = None
    affected_versions: Optional[List[str]] = None
    more_info_url: Optional[str] = None

    def get_advisory(self):
        return (
            self.advisory.replace("\r", "")
            if self.advisory
            else "No advisory found for this vulnerability."
        )

    def to_model_dict(self):
        try:
            affected_spec = next(iter(self.vulnerable_spec))
        except Exception:
            affected_spec = ""

        repr = {
            "id": self.vulnerability_id,
            "vulnerable_spec": affected_spec,
        }

        if self.ignored:
            repr["ignored"] = {
                "code": self.ignored_code,
                "reason": self.ignored_reason,
                "expires": self.ignored_expires,
            }

        return repr
