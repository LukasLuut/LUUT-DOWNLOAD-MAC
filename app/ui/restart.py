"""Relaunching the application (used by "Redefinir aplicativo")."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess

from app.utils.paths import is_fresh_profile

RESET_FLAG = "--reset-data"
FRESH_FLAG = "--fresh-start"


def restart_application(extra_args: list[str]) -> bool:
    """Start a new instance with `extra_args`. The new instance waits for this one to exit."""
    if getattr(sys, "frozen", False):
        program, args = sys.executable, []
    else:
        program, args = sys.executable, [str(Path(sys.argv[0]).resolve())]
    if is_fresh_profile():
        args.append(FRESH_FLAG)
    ok, _pid = QProcess.startDetached(program, args + extra_args)
    return bool(ok)
