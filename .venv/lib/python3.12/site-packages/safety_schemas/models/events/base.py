from typing import Generic, Optional, TypeVar
from typing_extensions import Annotated
import uuid
from pydantic import UUID4, BaseModel, BeforeValidator, Field

from .types import EventTypeBase, SourceType


class PayloadBase(BaseModel):
    """
    Base class for all event payloads
    """
    pass


# Generics
PayloadT = TypeVar("PayloadT", bound=PayloadBase)
EventTypeT = TypeVar("EventTypeT", bound=EventTypeBase)


def convert_to_source_type(v):
    if isinstance(v, str):
        try:
            return SourceType(v)
        except ValueError:
            pass
    return v


class Event(BaseModel, Generic[EventTypeT, PayloadT]):
    id: UUID4 = Field(default_factory=lambda: uuid.uuid4())
    timestamp: int
    type: EventTypeT
    source: Annotated[SourceType, BeforeValidator(convert_to_source_type)]
    correlation_id: Optional[str] = Field(
        description="Unique identifier for tracing related events",
        default=None,
    )    
    payload: PayloadT
