"""Shared utility functions for Tuya BLE Mesh integration.

This module provides reusable helper functions to avoid code duplication
across sensor, diagnostics, and other components.
"""

from __future__ import annotations


def connection_quality(rssi: int | float | None) -> str | None:
    """Map RSSI to a connection quality label.

    Args:
        rssi: RSSI value in dBm, or None.

    Returns:
        Quality classification: "good" (RSSI ≥ -60), "marginal" (RSSI -80 to -61),
        "poor" (RSSI < -80), or None when RSSI is unavailable.

    Examples:
        >>> connection_quality(-50)
        'good'
        >>> connection_quality(-70)
        'marginal'
        >>> connection_quality(-90)
        'poor'
        >>> connection_quality(None)
        None
    """
    if rssi is None:
        return None
    if rssi >= -60:
        return "good"
    if rssi >= -80:
        return "marginal"
    return "poor"
