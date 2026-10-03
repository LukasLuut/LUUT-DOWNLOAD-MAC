"""System-wide hotkeys (Windows RegisterHotKey), driven by the ShortcutManager.

Windows delivers WM_HOTKEY to a hidden native window owned by this process; nothing polls the keyboard and no key is
read outside the registered combinations. When a combination is already taken by another program, Windows refuses it
and the Atalhos tab shows it as unavailable.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from app.core.shortcuts import FUNCTION_KEYS, KeyCombo, ShortcutAction, ShortcutManager
from app.infrastructure.logger import get_logger

log = get_logger("hotkeys")

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
_MODIFIER_FLAGS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Win": MOD_WIN}
_NAMED_VK = {
    "Enter": 0x0D, "Space": 0x20, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22, "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "Escape": 0x1B,
}
# US-layout fallback for punctuation (the current keyboard layout is asked first, which covers ABNT2).
_PUNCTUATION_VK = {";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB,
                   "\\": 0xDC, "]": 0xDD, "'": 0xDE}


def virtual_key(key: str) -> int | None:
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return ord(key.upper())
    if key in FUNCTION_KEYS:
        return 0x70 + int(key[1:]) - 1
    if key in _NAMED_VK:
        return _NAMED_VK[key]
    if key in _PUNCTUATION_VK:
        if sys.platform == "win32":
            try:
                scan = ctypes.windll.user32.VkKeyScanW(ctypes.c_wchar(key))
                if scan != -1 and (scan >> 8) & 0xFF == 0:  # reachable without Shift/AltGr on this layout
                    return scan & 0xFF
            except (OSError, AttributeError):
                pass
        return _PUNCTUATION_VK[key]
    return None


def modifier_flags(combo: KeyCombo) -> int:
    flags = MOD_NOREPEAT
    for modifier in combo.modifiers:
        flags |= _MODIFIER_FLAGS[modifier]
    return flags


class HotkeyBackend:
    """Registers (id, modifiers, vk) with the OS and reports activations through `callback(id)`."""

    callback: Callable[[int], None] | None = None

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        return False

    def unregister(self, hotkey_id: int) -> None:
        pass

    def close(self) -> None:
        pass


class _HotkeyWindow(QWidget):
    """Never shown; only provides the native window that receives WM_HOTKEY."""

    def __init__(self, on_hotkey: Callable[[int], None]) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey
        self.setWindowTitle("Luut hotkeys")

    def nativeEvent(self, event_type, message):  # noqa: N802
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class WindowsHotkeyBackend(HotkeyBackend):
    def __init__(self) -> None:
        self._window = _HotkeyWindow(self._dispatch)
        self._hwnd = int(self._window.winId())
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32 = user32

    def _dispatch(self, hotkey_id: int) -> None:
        if self.callback:
            self.callback(hotkey_id)

    def register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        return bool(self._user32.RegisterHotKey(self._hwnd, hotkey_id, modifiers, vk))

    def unregister(self, hotkey_id: int) -> None:
        self._user32.UnregisterHotKey(self._hwnd, hotkey_id)

    def close(self) -> None:
        self._window.deleteLater()


def default_backend() -> HotkeyBackend:
    from PySide6.QtGui import QGuiApplication

    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return HotkeyBackend()  # macOS / offscreen tests: global hotkeys unavailable, reported honestly
    try:
        return WindowsHotkeyBackend()
    except (OSError, AttributeError) as exc:
        log.warning("Global hotkeys unavailable: %s", exc)
        return HotkeyBackend()


ActionFilter = Callable[[ShortcutAction], bool]


class GlobalHotkeyService(QObject):
    """Keeps Windows registrations in sync with the ShortcutManager's global bindings."""

    def __init__(self, manager: ShortcutManager, backend: HotkeyBackend | None = None,
                 action_filter: ActionFilter | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._backend = backend or default_backend()
        self._backend.callback = self._on_hotkey
        self._filter = action_filter or (lambda _action: True)
        self._registered: dict[int, tuple[str, str]] = {}  # hotkey id -> (action id, combo text)
        manager.add_listener(self._on_shortcuts_changed)
        self.sync()

    def _on_shortcuts_changed(self, _changed: set[str]) -> None:
        self.sync()

    def sync(self) -> None:
        """Register exactly the active global bindings that apply now (e.g. widget ones only when it is enabled)."""
        wanted: dict[str, KeyCombo] = {}
        for action in [] if self._manager.capturing else self._manager.global_actions():
            combo = self._manager.combo(action.id)
            if combo is not None and self._filter(action):
                wanted[action.id] = combo
        current = {action_id: combo_text for action_id, combo_text in self._registered.values()}
        if current == {a: c.text for a, c in wanted.items()}:
            return
        self.unregister_all()
        for index, (action_id, combo) in enumerate(sorted(wanted.items()), start=1):
            vk = virtual_key(combo.key)
            ok = vk is not None and self._backend.register(index, modifier_flags(combo), vk)
            self._manager.set_global_status(action_id, ok)
            if ok:
                self._registered[index] = (action_id, combo.text)
                log.info("Global shortcut registered: %s = %s", action_id, combo.text)
            else:
                log.warning("Global shortcut not available: %s = %s", action_id, combo.text)
        for action in self._manager.global_actions():
            if action.id not in wanted:
                self._manager.set_global_status(action.id, None)

    def unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            self._backend.unregister(hotkey_id)
        self._registered.clear()

    def registered_actions(self) -> dict[str, str]:
        return {action_id: combo for action_id, combo in self._registered.values()}

    def _on_hotkey(self, hotkey_id: int) -> None:
        entry = self._registered.get(hotkey_id)
        if entry:
            self._manager.trigger(entry[0])

    def close(self) -> None:
        self.unregister_all()
        self._manager.remove_listener(self._on_shortcuts_changed)
        self._backend.close()
