from pathlib import Path
from typing import Optional

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from ..report.schemas.v3_0 import main as v3_0
from .base import PolicySource, SafetyBaseModel
from .config import ConfigModel


@dataclass
class PolicyFileModel(SafetyBaseModel):
    id: str
    source: PolicySource
    location: Optional[Path]
    config: Optional[ConfigModel] = None

    def as_v30(self) -> v3_0.Policy:
        source_obj = (
            v3_0.PolicySource.local
            if self.source is PolicySource.local
            else v3_0.PolicySource.cloud
        )
        path = None

        if self.location:
            path = str(self.location.resolve())

        return v3_0.Policy(id=self.id, path=path, source=source_obj)

    @classmethod
    def from_v30(cls, obj: v3_0.SchemaModelV30) -> 'PolicyFileModel':
        
        if not isinstance(obj, v3_0.Policy):
            raise TypeError('Expected instance of v3_0.Policy')

        file_location = Path(obj.path) if obj.path else None

        return PolicyFileModel(
            id=obj.id, source=PolicySource(obj.source.value), location=file_location
        )
