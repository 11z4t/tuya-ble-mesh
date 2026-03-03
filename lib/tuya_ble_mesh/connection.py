"""BLE connection manager for Telink mesh devices.

Manages the BLE transport lifecycle including connect/disconnect with retry,
keep-alive, disconnect detection, and session key storage.

SECURITY: Session keys are zero-filled before clearing references on disconnect.
Key material is NEVER logged — only operation names and lengths.
"""

from __future__ import annotations

import enum


class ConnectionState(enum.Enum):
    """BLE connection state machine states.

    State transitions::

        DISCONNECTED ──connect()──→ CONNECTING
        CONNECTING ──BLE success──→ PAIRING
        PAIRING ──session key──→ READY
        READY ──disconnect detected──→ DISCONNECTING
        DISCONNECTING ──cleanup done──→ DISCONNECTED

        CONNECTING ──all retries failed──→ DISCONNECTED
        PAIRING ──provision failed──→ DISCONNECTED
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PAIRING = "pairing"
    READY = "ready"
    DISCONNECTING = "disconnecting"
