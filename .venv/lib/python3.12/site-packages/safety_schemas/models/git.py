from typing import Optional

from pydantic.dataclasses import dataclass


@dataclass
class GITModel:
    branch: Optional[str] = None
    tag: Optional[str] = None
    commit: Optional[str] = None
    dirty: Optional[bool] = None
    origin: Optional[str] = None
