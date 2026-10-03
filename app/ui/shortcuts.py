"""Qt side of the ShortcutManager: QShortcuts, key capture and shortcut hints on buttons.

Components never create QShortcuts with fixed keys: they bind action ids here and the binder follows every change
made in Configurações → Atalhos at runtime.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QWidget
from shiboken6 import isValid

from app.core.shortcuts import MODIFIERS, PUNCTUATION, KeyCombo, ShortcutManager
from app.infrastructure.logger import get_logger

log = get_logger("ui.shortcuts")

_QT_TOKENS = {"Win": "Meta", "Enter": "Return", "Delete": "Del", "Escape": "Esc", "PageUp": "PgUp",
              "PageDown": "PgDown", "Insert": "Ins"}


def qt_sequences(combo: KeyCombo) -> list[QKeySequence]:
    parts = [_QT_TOKENS.get(m, m) for m in MODIFIERS if m in combo.modifiers]
    keys = [_QT_TOKENS.get(combo.key, combo.key)]
    if combo.key == "Enter":
        keys.append("Enter")  # numeric keypad Enter
    return [QKeySequence.fromString("+".join(parts + [key]), QKeySequence.SequenceFormat.PortableText) for key in keys]


# ----------------------------------------------------------------------------- capture
_K = Qt.Key
_NAMED = {
    _K.Key_Return: "Enter", _K.Key_Enter: "Enter", _K.Key_Space: "Space", _K.Key_Backspace: "Backspace",
    _K.Key_Delete: "Delete", _K.Key_Insert: "Insert", _K.Key_Home: "Home", _K.Key_End: "End",
    _K.Key_PageUp: "PageUp", _K.Key_PageDown: "PageDown", _K.Key_Up: "Up", _K.Key_Down: "Down",
    _K.Key_Left: "Left", _K.Key_Right: "Right", _K.Key_Escape: "Escape", _K.Key_Comma: ",", _K.Key_Period: ".",
    _K.Key_Slash: "/", _K.Key_Semicolon: ";", _K.Key_Apostrophe: "'", _K.Key_BracketLeft: "[",
    _K.Key_BracketRight: "]", _K.Key_Backslash: "\\", _K.Key_Minus: "-", _K.Key_Equal: "=", _K.Key_QuoteLeft: "`",
}
_MODIFIER_KEYS = {_K.Key_Control, _K.Key_Shift, _K.Key_Alt, _K.Key_Meta, _K.Key_AltGr, _K.Key_Super_L,
                  _K.Key_Super_R}


def _token_from_virtual_key(vk: int) -> str | None:
    """Layout-independent reading on Windows (Shift+1 is "1", not "!"; ABNT2 punctuation via the active layout)."""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return str(vk - 0x60)  # numeric keypad digits
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if sys.platform == "win32" and 0xBA <= vk <= 0xE2:
        try:
            char = ctypes.windll.user32.MapVirtualKeyW(vk, 2) & 0xFFFF  # MAPVK_VK_TO_CHAR, current layout
        except (OSError, AttributeError):
            return None
        text = chr(char) if char else ""
        return text if text in PUNCTUATION else None
    return None


@dataclass(frozen=True)
class CapturedKeys:
    modifiers: frozenset[str]
    key: str | None          # None while only modifiers are held
    unsupported: bool = False

    @property
    def display(self) -> str:
        names = [m for m in MODIFIERS if m in self.modifiers]
        if self.key:
            names.append(self.key)
        return " + ".join(names) + ("" if self.key else " + …" if names else "")

    @property
    def text(self) -> str:
        return "+".join([m for m in MODIFIERS if m in self.modifiers] + ([self.key] if self.key else []))


def capture_from_event(event: QKeyEvent) -> CapturedKeys:
    mods = event.modifiers()
    modifiers = set()
    if mods & Qt.KeyboardModifier.ControlModifier:
        modifiers.add("Ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        modifiers.add("Alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        modifiers.add("Shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        modifiers.add("Win")
    key = event.key()
    if key in _MODIFIER_KEYS or key == 0:
        held = {_K.Key_Control: "Ctrl", _K.Key_Shift: "Shift", _K.Key_Alt: "Alt", _K.Key_Meta: "Win",
                _K.Key_Super_L: "Win", _K.Key_Super_R: "Win"}.get(Qt.Key(key)) if key else None
        if held:
            modifiers.add(held)  # the key being pressed is not always part of event.modifiers() yet
        return CapturedKeys(frozenset(modifiers), None)
    token = _NAMED.get(Qt.Key(key)) if key in _NAMED else None
    if token is None and sys.platform == "win32" and event.nativeVirtualKey():  # macOS key codes are not VKs
        token = _token_from_virtual_key(event.nativeVirtualKey())
    if token is None:
        if _K.Key_A <= key <= _K.Key_Z:
            token = chr(key)
        elif _K.Key_0 <= key <= _K.Key_9:
            token = chr(key)
        elif _K.Key_F1 <= key <= _K.Key_F24:
            token = f"F{key - _K.Key_F1 + 1}"
    if token is None:
        return CapturedKeys(frozenset(modifiers), None, unsupported=True)
    return CapturedKeys(frozenset(modifiers), token)


# ------------------------------------------------------------------------------ binder
Guard = Callable[[], bool]


@dataclass
class _Target:
    action_id: str
    widget: QWidget
    context: Qt.ShortcutContext


class ShortcutBinder(QObject):
    """Creates the QShortcuts of one window from the manager's current bindings (and rebuilds them on change)."""

    def __init__(self, manager: ShortcutManager, window: str, owner: QWidget, guard: Guard | None = None) -> None:
        super().__init__(owner)
        self._manager = manager
        self._window = window
        self._owner = owner
        self._guard = guard or (lambda: True)
        self._targets: dict[str, _Target] = {}
        self._shortcuts: list[QShortcut] = []
        self._unregister: list[Callable[[], None]] = []
        self._hints: list[tuple[QWidget, str, str | None, QLabel | None]] = []
        manager.add_listener(self._on_changed)
        owner.destroyed.connect(self._detach)

    def bind(self, action_id: str, handler: Callable[[], object], widget: QWidget | None = None,
             context: Qt.ShortcutContext = Qt.ShortcutContext.WindowShortcut) -> None:
        """`widget`/`context` narrow where it fires (e.g. Enter only inside the URL field)."""
        self._unregister.append(self._manager.register_handler(action_id, handler))
        self._targets[action_id] = _Target(action_id, widget or self._owner, context)

    def hint(self, widget: QWidget, action_id: str, label: str | None = None, caption: QLabel | None = None) -> None:
        """Keep a tooltip (and optionally a small caption label) showing the configured combination."""
        self._hints.append((widget, action_id, label, caption))
        self._apply_hint(widget, action_id, label, caption)

    def apply(self) -> None:
        for shortcut in self._shortcuts:
            if isValid(shortcut):
                shortcut.setEnabled(False)
                shortcut.deleteLater()
        self._shortcuts.clear()
        groups: dict[tuple[int, int, str], list[str]] = {}
        widgets: dict[int, QWidget] = {}
        for combo_text, action_ids in self._manager.bindings_for_window(self._window).items():
            for action_id in action_ids:
                target = self._targets.get(action_id)
                if target is None:
                    continue
                key = (id(target.widget), int(target.context.value), combo_text)
                widgets[id(target.widget)] = target.widget
                groups.setdefault(key, []).append(action_id)
        for (widget_id, context_value, _combo_text), action_ids in groups.items():
            combo = self._manager.combo(action_ids[0])
            if combo is None:
                continue
            for sequence in qt_sequences(combo):
                shortcut = QShortcut(sequence, widgets[widget_id])
                shortcut.setContext(Qt.ShortcutContext(context_value))
                shortcut.setAutoRepeat(False)
                shortcut.activated.connect(lambda ids=tuple(action_ids): self._activate(ids))
                self._shortcuts.append(shortcut)
        for widget, action_id, label, caption in self._hints:
            self._apply_hint(widget, action_id, label, caption)

    def _activate(self, action_ids: tuple[str, ...]) -> None:
        if not self._guard():
            return
        self._manager.trigger_first(action_ids)

    def _apply_hint(self, widget: QWidget, action_id: str, label: str | None, caption: QLabel | None) -> None:
        if not isValid(widget):
            return
        widget.setToolTip(self._manager.hint(action_id, label))
        if caption is not None and isValid(caption):
            combo = self._manager.combo(action_id)
            caption.setText(combo.display if combo else "")
            caption.setVisible(combo is not None)

    def _on_changed(self, _changed: set[str]) -> None:
        if isValid(self._owner):
            self.apply()

    def _detach(self, *_: object) -> None:
        self._manager.remove_listener(self._on_changed)
        for unregister in self._unregister:
            unregister()
        self._unregister.clear()
