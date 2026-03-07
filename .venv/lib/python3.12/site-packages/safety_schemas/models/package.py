import abc
from dataclasses import field
from pathlib import Path
from typing import List, Optional

from pydantic.dataclasses import dataclass

from .specification import PythonSpecification

NOT_IMPLEMENTED_ERROR_MSG = (
    "Needs implementation for the specific " "specification type."
)


@dataclass
class Dependency:
    name: str
    version: Optional[str]
    specifications: List[PythonSpecification]
    found: Optional[Path] = None
    absolute_path: Optional[Path] = None
    insecure_versions: List[str] = field(default_factory=lambda: [])
    secure_versions: List[str] = field(default_factory=lambda: [])
    latest_version_without_known_vulnerabilities: Optional[str] = None
    latest_version: Optional[str] = None
    more_info_url: Optional[str] = None

    def has_unpinned_specification(self):
        for specification in self.specifications:
            if not specification.is_pinned():
                return True
        return False

    def get_unpinned_specificaitons(self):
        return filter(
            lambda specification: not specification.is_pinned(), self.specifications
        )

    @abc.abstractmethod
    def filter_by_supported_versions(self, versions: List[str]) -> List[str]:
        raise NotImplementedError()

    @abc.abstractmethod
    def get_versions(self, db_full):
        raise NotImplementedError()

    @abc.abstractmethod
    def refresh_from(self, db_full):
        raise NotImplementedError()

    def to_dict(self, **kwargs):
        if kwargs.get("short_version", False):
            return {
                "name": self.name,
                "version": self.version,
                "requirements": self.specifications,
            }

        return {
            "name": self.name,
            "version": self.version,
            "requirements": self.specifications,
            "found": None,
            "insecure_versions": self.insecure_versions,
            "secure_versions": self.secure_versions,
            "latest_version_without_known_vulnerabilities": self.latest_version_without_known_vulnerabilities,  # noqa: E501
            "latest_version": self.latest_version,
            "more_info_url": self.more_info_url,
        }

    def update(self, new):
        for key, value in new.items():
            if hasattr(self, key):
                setattr(self, key, value)


@dataclass
class PythonDependency(Dependency):
    def filter_by_supported_versions(self, versions: List[str]) -> List[str]:
        from packaging.version import parse as parse_version

        allowed = []

        for version in versions:
            try:
                parse_version(version)
                allowed.append(version)
            except Exception:
                pass

        return allowed

    def get_versions(self, db_full):
        pkg_meta = db_full.get("meta", {}).get("packages", {}).get(self.name, {})
        versions = self.filter_by_supported_versions(
            pkg_meta.get("insecure_versions", []) + pkg_meta.get("secure_versions", [])
        )
        return set(versions)

    def refresh_from(self, db_full):
        from packaging.utils import canonicalize_name

        base_domain = db_full.get("meta", {}).get("base_domain")
        pkg_meta = (
            db_full.get("meta", {})
            .get("packages", {})
            .get(canonicalize_name(self.name), {})
        )

        kwargs = {
            "insecure_versions": self.filter_by_supported_versions(
                pkg_meta.get("insecure_versions", [])
            ),
            "secure_versions": self.filter_by_supported_versions(
                pkg_meta.get("secure_versions", [])
            ),
            "latest_version_without_known_vulnerabilities": pkg_meta.get(
                "latest_secure_version", None
            ),
            "latest_version": pkg_meta.get("latest_version", None),
            "more_info_url": f"{base_domain}{pkg_meta.get('more_info_path', '')}",
        }

        self.update(kwargs)

    @staticmethod
    def find_version(specifications: List[PythonSpecification]) -> Optional[str]:
        ver = None

        if len(specifications) != 1:
            return ver

        specification = specifications[0]
        if specification.is_pinned():
            ver = next(iter(specification.specifier)).version

        return ver
