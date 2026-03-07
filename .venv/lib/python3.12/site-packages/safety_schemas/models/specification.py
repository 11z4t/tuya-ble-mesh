import abc
from dataclasses import InitVar, field
from pathlib import Path
from typing import List, Optional, Union

from dparse import filetypes as parse_strategy
from dparse import parse as parse_specification
from dparse.dependencies import Dependency as ParsedDependency
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from pydantic import VERSION as pydantic_version
from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import Annotated, ClassVar

try:
    from pydantic_core import ArgsKwargs
except ImportError:
    pass

from .vulnerability import RemediationModel, Vulnerability

NOT_IMPLEMENTED_ERROR_MSG = (
    "Needs implementation for the specific " "specification type."
)


@dataclass
class Specification(metaclass=abc.ABCMeta):
    raw: str
    found: Optional[Path]
    vulnerabilities: List[Vulnerability] = field(default_factory=lambda: [])
    remediation: Optional[RemediationModel] = None

    @abc.abstractmethod
    def is_pinned(self) -> bool:
        raise NotImplementedError(NOT_IMPLEMENTED_ERROR_MSG)

    @abc.abstractmethod
    def is_vulnerable(self, *args, **kwargs) -> bool:
        raise NotImplementedError(NOT_IMPLEMENTED_ERROR_MSG)


def get_dep(specification: Union[str, ParsedDependency]):
    _dep = specification

    if isinstance(specification, str):
        deps = parse_specification(
            specification, file_type=parse_strategy.requirements_txt
        ).dependencies
        _dep = deps[0] if deps else None

    if not isinstance(_dep, ParsedDependency):
        raise ValueError(
            f"The '{specification}' specification is "
            "not a valid Python specificaiton."
        )

    return _dep


@dataclass(config={"arbitrary_types_allowed": True})
class PythonSpecification(Requirement, Specification):
    dep: ClassVar[Optional[ParsedDependency]] = Field(default=None, exclude=True)

    def __load_req(self, specification: Union[str, ParsedDependency]):
        self.dep = get_dep(specification)

        raw_line = self.dep.line
        to_parse = self.dep.line
        # Hash and comments are only a pip feature, so removing them.
        if "#" in to_parse:
            to_parse = self.dep.line.split("#")[0]

        for req_hash in self.dep.hashes:
            to_parse = to_parse.replace(req_hash, "")

        to_parse = to_parse.replace("\\", "").rstrip()

        try:
            # Try to build a PEP Requirement from the cleaned line
            super().__init__(to_parse)
        except Exception:
            raise ValueError(
                f"The '{raw_line}' specification is "
                "not a valid Python specificaiton."
            )

    if not pydantic_version.startswith("1."):
        from pydantic import model_validator

        @model_validator(mode='before')
        def pre_root(cls, values):
            args, kwargs = values.args, values.kwargs

            try:
                specification = args[0]
            except IndexError:
                raise ValueError('Specification is required')

            _dep = get_dep(specification)

            return ArgsKwargs((), {'raw': _dep.line, 'found': None if not kwargs else kwargs.get('found', None), 'dep': _dep})

        def __post_init__(self):
            self.__load_req(specification=self.raw)
    else:
        def __init__(
            self, specification: Union[str, ParsedDependency], found: Optional[Path] = None
        ) -> None:
            self.__load_req(specification=specification)
            self.raw = self.dep.line
            self.found = found

    def __eq__(self, other):
        return str(self) == str(other)

    def is_pinned(self) -> bool:
        if not self.specifier or len(self.specifier) != 1:
            return False

        specifier = next(iter(self.specifier))

        return (
            specifier.operator == "==" and "*" != specifier.version[-1]
        ) or specifier.operator == "==="

    def is_vulnerable(
        self, vulnerable_spec: SpecifierSet, insecure_versions: List[str]
    ):
        if self.is_pinned():
            try:
                return vulnerable_spec.contains(next(iter(self.specifier)).version, prereleases=True)
            except Exception:
                # Ugly for now...
                return False

        return any(
            self.specifier.filter(
                vulnerable_spec.filter(insecure_versions, prereleases=True),
                prereleases=True,
            )
        )

    def to_dict(self, **kwargs):
        specifier_obj = self.specifier
        if "specifier_obj" not in kwargs:
            specifier_obj = str(self.specifier)

        return {
            "raw": self.raw,
            "extras": list(self.extras),
            "marker": str(self.marker) if self.marker else None,
            "name": self.name,
            "specifier": specifier_obj,
            "url": self.url,
            "found": self.found,
        }
