"""Ensure lib/tuya_ble_mesh is importable from both installed and dev layouts.

Call ensure_lib_importable() once from __init__.py — it is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_lib_importable() -> None:
    """Add the bundled (or dev) lib directory to sys.path if needed."""
    _BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
    _DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
    for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
        if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
            sys.path.insert(0, _lib_dir)
            break
