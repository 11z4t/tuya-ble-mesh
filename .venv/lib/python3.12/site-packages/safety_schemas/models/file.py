from dataclasses import asdict, field
from pathlib import Path
from typing import List

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from .util import dict_dump
from ..report.schemas.v3_0 import main as v3_0
from .base import FileType, SafetyBaseModel
from .package import PythonDependency, PythonSpecification
from .result import DependencyResultModel
from .vulnerability import ClosestSecureVersion, RemediationModel, Vulnerability


@dataclass
class FileModel(SafetyBaseModel):
    location: Path
    file_type: FileType
    results: DependencyResultModel = field(
        default_factory=lambda: DependencyResultModel(dependencies=[])
    )

    def as_v30(self) -> v3_0.File:
        dependencies_output = []

        for dep in self.results.dependencies:
            specs: List[v3_0.AnalyzedSpecification] = []
            for specification in dep.specifications:
                rem = None

                if specification.remediation:
                    closest = None
                    if specification.remediation.closest_secure:
                        closest_kwargs = asdict(
                            specification.remediation.closest_secure
                        )
                        closest = v3_0.ClosestSecureSpecification(**closest_kwargs)

                    rem = v3_0.Remediation(
                        vulnerabilities_found=specification.remediation.vulnerabilities_found,
                        closest_secure=closest,
                        recommended=specification.remediation.recommended,
                        other_recommended=specification.remediation.other_recommended,
                    )

                analyzed = v3_0.AnalyzedSpecification(
                    raw=specification.raw,
                    vulnerabilities=v3_0.SpecificationVulnerabilities(
                        known_vulnerabilities=[
                            v3_0.Vulnerability(**vuln.to_model_dict())
                            for vuln in specification.vulnerabilities
                        ],
                        remediation=rem,
                    ),
                )
                specs.append(analyzed)

            p = v3_0.Package(name=dep.name, specifications=specs)
            dependencies_output.append(p)

        return v3_0.File(
            location=str(self.location),
            type=self.file_type.value,
            categories=[self.file_type.ecosystem.value],
            results=v3_0.Results(dependencies=dependencies_output),
        )
        
    @classmethod
    def from_v30(cls, obj: v3_0.SchemaModelV30) -> 'FileModel':
        if not isinstance(obj, v3_0.File):
            raise TypeError('Expected instance of v3_0.File')

        location = Path(obj.location)

        dependencies: List[PythonDependency] = []

        for dep in obj.results.dependencies:
            specs: List[PythonSpecification] = []
            for specification in dep.specifications:
                remediation_obj = None
                remed = specification.vulnerabilities.remediation
                if remed:
                    closest_sec = None

                    if remed.closest_secure:
                        closest_sec = ClosestSecureVersion(
                            **dict_dump(remed.closest_secure)
                        )

                    remediation_obj = RemediationModel(
                        vulnerabilities_found=remed.vulnerabilities_found,
                        more_info_url="",
                        recommended=remed.recommended,
                        closest_secure=closest_sec,
                        other_recommended=remed.other_recommended,
                    )

                vulns: List[Vulnerability] = []

                py_spec = PythonSpecification(specification.raw, found=location)

                for vuln in specification.vulnerabilities.known_vulnerabilities:
                    ignored = False
                    ignored_expires = None
                    ignored_reason = None
                    ignored_code = None

                    if vuln.ignored:
                        ignored = True
                        ignored_expires = vuln.ignored.expires
                        ignored_reason = vuln.ignored.reason
                        ignored_code = vuln.ignored.code

                    vulns.append(
                        Vulnerability(
                            vulnerability_id=vuln.id,
                            package_name=py_spec.name,
                            ignored=ignored,
                            ignored_reason=ignored_reason,
                            ignored_expires=ignored_expires,
                            ignored_code=ignored_code,
                            vulnerable_spec=vuln.vulnerable_spec,
                        )
                    )

                py_spec.remediation = remediation_obj
                py_spec.vulnerabilities = vulns
                specs.append(py_spec)

            version = PythonDependency.find_version(specifications=specs)
            dependencies.append(
                PythonDependency(
                    name=dep.name, version=version, specifications=specs, found=location
                )
            )

        results = DependencyResultModel(dependencies=dependencies)

        return FileModel(
            location=location, file_type=FileType(obj.type), results=results
        )
