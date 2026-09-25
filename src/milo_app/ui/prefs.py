"""Where the app remembers display choices (ticked descriptors, hidden node kinds, ...).

MILO_PROFILE picks a separate set, so test runs never touch the real one."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings


def store() -> QSettings:
    return QSettings("MILO", os.environ.get("MILO_PROFILE", "milo-app"))
