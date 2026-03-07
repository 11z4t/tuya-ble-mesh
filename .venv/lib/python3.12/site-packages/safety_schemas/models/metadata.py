from datetime import datetime
from pathlib import Path
from typing import Any, List, Union

from pydantic.dataclasses import dataclass
from typing_extensions import Self

from ..report.schemas.v3_0 import main as v3_0
from .base import (
    AuthenticationType,
    ReportSchemaVersion,
    SafetyBaseModel,
    ScanType,
    Stage,
)
from .telemetry import TelemetryModel


@dataclass
class MetadataModel(SafetyBaseModel):
    """
    Main data about the report, used for traceability purposes.
    """

    scan_type: ScanType
    stage: Stage
    scan_locations: List[Path]
    authenticated: bool
    authentication_type: AuthenticationType
    telemetry: TelemetryModel
    schema_version: ReportSchemaVersion
    timestamp: datetime = datetime.now()

    def as_v30(self, *args: Any, **kwargs: Any) -> v3_0.SchemaModelV30:
        auth_method = None

        if self.authentication_type is AuthenticationType.API_KEY:
            auth_method = v3_0.AuthenticationMethod.api_key
        elif self.authentication_type is AuthenticationType.TOKEN:
            auth_method = v3_0.AuthenticationMethod.token

        return v3_0.Meta(
            scan_type=v3_0.ScanType(self.scan_type.value),
            stage=v3_0.StageType(self.stage.value),
            scan_locations=[str(location) for location in self.scan_locations],
            authenticated=self.authenticated,
            authentication_method=auth_method,
            timestamp=self.timestamp,
            telemetry=self.telemetry.as_v30(),
            schema_version=self.schema_version.value,
        )

    @classmethod
    def from_v30(cls, obj: Union[v3_0.SchemaModelV30, dict]) -> "MetadataModel":
        # Allow obj to be a dict or an instance of v3_0.Meta.
        if isinstance(obj, dict):
            # Check for a missing authentication_method and set the default.
            auth_value = obj.get("authentication_method")
            if auth_value is None:
                auth_value = "api_key" if obj.get("api_key", False) else "token"
            obj["authentication_method"] = auth_value

            # Create a v3_0.Meta instance from the dict.
            meta_obj = (
                v3_0.Meta.model_validate(obj)
                if hasattr(v3_0.Meta, "model_validate")
                else v3_0.Meta(**obj)
            )
        else:
            meta_obj = obj
            auth_value = meta_obj.authentication_method
            if auth_value is None:
                auth_value = (
                    "api_key" if getattr(meta_obj, "api_key", False) else "token"
                )

        return MetadataModel(
            scan_type=ScanType(meta_obj.scan_type.value),
            stage=Stage(meta_obj.stage.value),
            scan_locations=[Path(location) for location in meta_obj.scan_locations],
            authenticated=meta_obj.authenticated,
            authentication_type=AuthenticationType(auth_value),
            telemetry=TelemetryModel.from_v30(meta_obj.telemetry),
            schema_version=ReportSchemaVersion(meta_obj.schema_version),
            timestamp=meta_obj.timestamp,
        )
