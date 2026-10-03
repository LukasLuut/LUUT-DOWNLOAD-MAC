"""Windows-specific integration (dark title bar, taskbar identity)."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtWidgets import QWidget

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20


def apply_title_bar_theme(widget: QWidget, dark: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), _DWMWA_USE_IMMERSIVE_DARK_MODE,
                                                   ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError):
        pass


def set_app_user_model_id(app_id: str) -> None:
    """Groups the taskbar icon under our own identity instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (OSError, AttributeError):
        pass
