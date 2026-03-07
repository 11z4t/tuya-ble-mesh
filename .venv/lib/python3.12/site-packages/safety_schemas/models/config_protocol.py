from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import SafetyConfigBaseModel

from ..config.schemas.v3_0 import main as v3_0

NOT_IMPLEMENTED_ERROR_MSG = (
    "Needs implementation for the specific " "schema version export."
)


class ConfigConvertible(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def as_v30(self) -> v3_0.SchemaModelV30:
        raise NotImplementedError(NOT_IMPLEMENTED_ERROR_MSG)

    @classmethod
    @abc.abstractmethod
    def from_v30(cls, obj: v3_0.SchemaModelV30) -> SafetyConfigBaseModel:
        raise NotImplementedError(NOT_IMPLEMENTED_ERROR_MSG)
