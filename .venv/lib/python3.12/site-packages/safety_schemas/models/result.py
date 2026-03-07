from dataclasses import field
from typing import Dict, List

from pydantic.dataclasses import dataclass

from .base import IgnoreCodes, IgnoredItems
from .package import PythonDependency
from .specification import PythonSpecification
from .vulnerability import Vulnerability


def not_ignored(vuln):
    return not vuln.ignored


@dataclass
class DependencyResultModel:
    dependencies: List[PythonDependency]
    ignored_vulns: IgnoredItems = field(default_factory=lambda: IgnoredItems({}))
    ignored_vulns_data: Dict[str, Vulnerability] = field(default_factory=lambda: {})

    failed: bool = False

    def get_affected_specifications(
        self, include_ignored: bool = False
    ) -> List[PythonSpecification]:
        affected = []
        for dep in self.dependencies:
            affected += [
                spec
                for spec in dep.specifications
                if (any(spec.vulnerabilities) if include_ignored else any(filter(not_ignored, spec.vulnerabilities)))
            ]
        return affected

    def get_affected_dependencies(self) -> List[PythonDependency]:
        affected = []
        for dep in self.dependencies:
            for spec in dep.specifications:
                if any(filter(not_ignored, spec.vulnerabilities)):
                    affected.append(dep)
                    break
        return affected
