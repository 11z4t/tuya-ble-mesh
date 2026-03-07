from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from ..base import PayloadBase
from ..types import LimitedStr, ToolType
from .main import ToolStatus

from enum import Enum


class InitStartedPayload(PayloadBase):
    """
    Payload for the Init Started event.
    This is emitted when the init command is started.
    Note: This event is typically delayed until the user completes authentication.
    """
    # This is an empty payload as the timestamp is already in the event
    pass


class AuthStartedPayload(PayloadBase):
    """
    Payload for the Auth Started event.
    This is emitted when the authentication flow is initiated and a URL is shown to the user.
    """
    auth_url: Optional[LimitedStr] = Field(
        default=None, description="URL provided to the user for authentication"
    )


class AuthCompletedPayload(PayloadBase):
    """
    Payload for the Auth Completed event.
    This is emitted when the authentication flow is completed.
    """
    success: bool = Field(description="Whether authentication was successful")
    error_message: Optional[LimitedStr] = Field(
        default=None, description="Error message if authentication failed"
    )


class FirewallSetupResponseCreatedPayload(PayloadBase):
    """
    Payload for the Firewall Setup Response Created event.
    This captures the user's choice to install the firewall (Y/N).
    """
    user_consent_requested: bool = Field(
        description="Whether the user was asked for consent to install the firewall"
    )
    user_consent: Optional[bool] = Field(
        default=None, description="User's consent to install the firewall (True for yes, False for no, None if unknown)"
    )


class FirewallSetupCompletedPayload(PayloadBase):
    """
    Payload for the Firewall Setup Completed event.
    This is emitted when the firewall is configured. This payload has the current status of all tools.
    """
    tools: List[ToolStatus] = Field(
        description="Status of all configured package manager tools"
    )


class DependencyFile(BaseModel):
    """
    Information about a detected dependency file.
    """
    file_path: str = Field(description="Path to the detected dependency file")


class CodebaseDetectionStatusPayload(PayloadBase):
    """
    Payload for the Codebase Detection Status event.
    This is emitted when the codebase is detected.
    """
    detected: bool = Field(description="Whether a codebase was detected")
    dependency_files: Optional[List[DependencyFile]] = Field(
        default=None, description="List of detected dependency files"
    )


class CodebaseSetupResponseCreatedPayload(PayloadBase):
    """
    Payload for the Codebase Setup Response Created event.
    This captures the user's choice to add a codebase (Y/N).
    """
    user_consent_requested: bool = Field(
        description="Whether the user was asked for consent to add a codebase"
    )
    user_consent: Optional[bool] = Field(
        default=None, description="User's consent to add a codebase (True for yes, False for no, None if unknown)"
    )


class CodebaseSetupCompletedPayload(PayloadBase):
    """
    Payload for the Codebase Setup Completed event.
    This is emitted when a codebase is successfully created or verified.
    """
    is_created: bool = Field(description="Whether the codebase was created")
    codebase_id: Optional[str] = Field(default=None, description="ID of the codebase")


class InitScanCompletedPayload(PayloadBase):
    """
    Payload for the Init Scan Completed event.
    This is emitted when the initial scan completes.
    """
    scan_id: Optional[str] = Field(default=None, description="ID of the completed scan")


class InitExitStep(str, Enum):
    """
    Possible steps where the init process could be exited.
    """
    PRE_AUTH = "pre_authentication"
    POST_AUTH = "post_authentication"
    PRE_FIREWALL_SETUP = "pre_firewall_setup"
    POST_FIREWALL_SETUP = "post_firewall_setup"
    PRE_CODEBASE_SETUP = "pre_codebase_setup"
    POST_CODEBASE_SETUP = "post_codebase_setup"
    PRE_SCAN = "pre_scan"
    POST_SCAN = "post_scan"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class InitExitedPayload(PayloadBase):
    """
    Payload for the Init Exited event.
    This is emitted when the user exits the init process (e.g., via Ctrl+C).
    """
    exit_step: InitExitStep = Field(
        description="The last step known before the user exited"
    )
