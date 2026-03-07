from typing import ClassVar, Dict, List, Type
from typing_extensions import Annotated
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from ..events import Event, EventContext
from ..events import EventType
from ..events.payloads import (
    CommandExecutedPayload,
    CommandErrorPayload,
    ToolCommandExecutedPayload,
    PackageInstalledPayload,
    PackageUninstalledPayload,
    PackageUpdatedPayload,
    FirewallHeartbeatPayload,
    FirewallConfiguredPayload,
    FirewallDisabledPayload,

    # Onboarding
    InitStartedPayload,
    AuthStartedPayload,
    AuthCompletedPayload,
    FirewallSetupResponseCreatedPayload,
    FirewallSetupCompletedPayload,
    CodebaseSetupResponseCreatedPayload,
    CodebaseSetupCompletedPayload,
    CodebaseDetectionStatusPayload,
    InitScanCompletedPayload,
    InitExitedPayload,
)
from ..events import PayloadBase

def convert_to_event_type(v):
    if isinstance(v, str):
        try:
            return EventType(v)
        except ValueError:
            pass
    return v


class EventApiPayload(Event):
    """
    Event object with added context information for the /events API endpoint.
    Extends the base Event to include information about the execution
    environment.
    """
    type: Annotated[EventType, BeforeValidator(convert_to_event_type)]
    context: EventContext

    model_config = ConfigDict(extra="allow", populate_by_name=True, strict=True)

    # Registry of event types to payload types
    payload_types: ClassVar[Dict[EventType, Type[PayloadBase]]] = {
        EventType.COMMAND_EXECUTED: CommandExecutedPayload,
        EventType.COMMAND_ERROR: CommandErrorPayload,
        EventType.TOOL_COMMAND_EXECUTED: ToolCommandExecutedPayload,
        EventType.PACKAGE_INSTALLED: PackageInstalledPayload,
        EventType.PACKAGE_UPDATED: PackageUpdatedPayload,
        EventType.PACKAGE_UNINSTALLED: PackageUninstalledPayload,
        EventType.FIREWALL_HEARTBEAT: FirewallHeartbeatPayload,
        EventType.FIREWALL_CONFIGURED: FirewallConfiguredPayload,
        EventType.FIREWALL_DISABLED: FirewallDisabledPayload,

        # Onboarding
        EventType.INIT_STARTED: InitStartedPayload,
        EventType.AUTH_STARTED: AuthStartedPayload,
        EventType.AUTH_COMPLETED: AuthCompletedPayload,
        EventType.FIREWALL_SETUP_RESPONSE_CREATED: FirewallSetupResponseCreatedPayload,
        EventType.FIREWALL_SETUP_COMPLETED: FirewallSetupCompletedPayload,
        EventType.CODEBASE_SETUP_RESPONSE_CREATED: CodebaseSetupResponseCreatedPayload,
        EventType.CODEBASE_SETUP_COMPLETED: CodebaseSetupCompletedPayload,
        EventType.CODEBASE_DETECTION_STATUS: CodebaseDetectionStatusPayload,
        EventType.INIT_SCAN_COMPLETED: InitScanCompletedPayload,
        EventType.INIT_EXITED: InitExitedPayload,
    }
    
    @model_validator(mode='before')
    @classmethod
    def validate_payload_type(cls, data):
        if not isinstance(data, dict):
            return data
        
        event_type = data.get('type')
        payload_data = data.get('payload')
        
        # Skip if either is missing
        if event_type is None or payload_data is None:
            return data
            
        # Convert to enum if it's a string
        if isinstance(event_type, str):
            try:
                event_type = EventType(event_type)
            except ValueError:
                pass
        
        # Get the appropriate payload class
        payload_cls = cls.payload_types.get(event_type)
        
        if payload_cls and isinstance(payload_data, dict):
            try:
                # Parse the payload with the appropriate model
                data['payload'] = payload_cls.model_validate(payload_data)
            except Exception as e:
                raise ValueError(f"Failed to parse payload for event type {event_type}: {e}")
        
        return data


class EventBatchApiPayload(BaseModel):
    """
    A batch of events for the /events API endpoint.
    Used for efficient transport of multiple events in a single
    request/response.
    """

    events: List[EventApiPayload]
