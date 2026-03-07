from typing import Dict, Optional

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from ..report.schemas.v3_0 import main as v3_0
from .base import SafetyBaseModel


@dataclass
class TelemetryModel(SafetyBaseModel):
    """
    Telemetry object generated per Safety report; this model holds data related to the
    client application running Safety CLI.
    """

    safety_options: Dict[str, Dict[str, int]]
    safety_version: str
    safety_source: str
    os_type: Optional[str] = None
    os_release: Optional[str] = None
    os_description: Optional[str] = None
    python_version: Optional[str] = None
    safety_command: Optional[str] = None

    def as_v30(self) -> v3_0.Telemetry:
        return v3_0.Telemetry(
            os_type=self.os_type,
            os_release=self.os_release,
            os_description=self.os_description,
            python_version=self.python_version,
            safety_command=self.safety_command,
            safety_options=self.safety_options,
            safety_version=self.safety_version,
            safety_source=self.safety_source,
        )

    @classmethod
    def from_v30(cls, obj: v3_0.SchemaModelV30) -> 'TelemetryModel':

        if not isinstance(obj, v3_0.Telemetry):
            raise TypeError('Expected instance of v3_0.Telemetry')
            
        return TelemetryModel(
            os_type=obj.os_type,
            os_release=obj.os_release,
            os_description=obj.os_description,
            python_version=obj.python_version,
            safety_command=obj.safety_command,
            safety_options=obj.safety_options,
            safety_version=obj.safety_version,
            safety_source=obj.safety_source,
        )
