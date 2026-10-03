"""Configurações → Atalhos: every command, grouped (desktop, downloads, widget, system), editable in place."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QPushButton, QWidget

from app import SYSTEM_NAME
from app.core.shortcuts import (
    GROUP_APP,
    GROUP_DOWNLOADS,
    GROUP_HINTS,
    GROUP_SYSTEM,
    GROUP_WIDGET,
    GROUPS,
    ShortcutConflict,
    ShortcutError,
    ShortcutManager,
)
from app.infrastructure.logger import get_logger
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.base import ThemedDialog
from app.ui.dialogs.confirm_dialog import ask
from app.ui.shortcuts import capture_from_event
from app.ui.widgets.common import (
    Card,
    Chip,
    Separator,
    hbox,
    icon_button,
    make_button,
    make_label,
    repolish,
    vbox,
)

log = get_logger("ui.shortcuts")
GROUP_ORDER = (GROUP_APP, GROUP_DOWNLOADS, GROUP_WIDGET, GROUP_SYSTEM)
DISABLE = "__disable__"


class ShortcutCaptureDialog(ThemedDialog):
    """"Pressione a combinação desejada…" — Esc cancels, Backspace/Delete leave the command without a shortcut."""

    def __init__(self, parent: QWidget | None, manager: ShortcutManager, action_id: str) -> None:
        super().__init__(parent, "Definir novo atalho")
        self._manager = manager
        self._action = manager.action(action_id)
        self.result_text: str | None = None
        self.setMinimumWidth(460)
        box = QFrame()
        box.setObjectName("CaptureBox")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.keys = make_label("Pressione a combinação desejada…", "Secondary")
        self.keys.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setLayout(vbox(self.keys, margins=(16, 22, 16, 22)))
        self.error = make_label("", "ErrorText", wrap=True)
        self.error.hide()
        scope = "Atalho GLOBAL — funciona mesmo com o Luut em segundo plano." if self._action.is_global else \
            "Atalho LOCAL — funciona com a janela em foco."
        disable = make_button("Desativar atalho", "ghost")
        disable.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        disable.clicked.connect(self._disable)
        cancel = make_button("Cancelar", None)
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel.clicked.connect(self.reject)
        self.setLayout(vbox(
            make_label("Definir novo atalho", "SectionTitle"),
            make_label(f"<b>{self._action.label}</b><br>{scope}", "Secondary", wrap=True),
            box, self.error,
            make_label("[ Esc ] Cancelar   •   [ Backspace ] ou [ Delete ] Desativar", "Muted"),
            hbox(None, disable, cancel, spacing=8), spacing=10, margins=(24, 22, 24, 20)))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showEvent(self, event) -> None:  # noqa: N802
        self._manager.set_capturing(True)  # nothing fires (and no global hotkey is held) while recording
        super().showEvent(event)
        self.setFocus()

    def done(self, result: int) -> None:
        self._manager.set_capturing(False)
        super().done(result)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and event.key() in (
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # every key goes to the capture
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        captured = capture_from_event(event)
        if captured.key == "Escape" and not captured.modifiers:
            self.reject()
            return
        if captured.key in ("Backspace", "Delete") and not captured.modifiers:
            self._disable()
            return
        if captured.unsupported:
            self._show_error("Essa tecla não é suportada. Escolha outra tecla principal.")
            return
        self.keys.setObjectName("CaptureKeys")
        repolish(self.keys)
        self.keys.setText(captured.display or "Pressione a combinação desejada…")
        if captured.key is None:
            return
        try:
            self._manager.validate(self._action.id, captured.text)
        except ShortcutError as error:
            self._show_error(error.message)
            return
        self.result_text = captured.text
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
        self.adjustSize()

    def _disable(self) -> None:
        self.result_text = DISABLE
        self.accept()


def edit_shortcut(parent: QWidget | None, context: AppContext, action_id: str) -> bool:
    """Full edit flow shared by the Atalhos tab and the Widget tab. Returns True when something changed."""
    manager = context.shortcuts
    dialog = ShortcutCaptureDialog(parent, manager, action_id)
    if not dialog.exec() or dialog.result_text is None:
        return False
    if dialog.result_text == DISABLE:
        manager.disable(action_id)
        return True
    try:
        manager.assign(action_id, dialog.result_text)
    except ShortcutConflict as conflict:
        choice = ask(parent, "Atalho em conflito", f"{conflict.message}\n\nDeseja substituir?",
                     [("cancel", "Cancelar", None), ("replace", "Substituir", "primary")])
        if choice != "replace":
            return False
        manager.assign(action_id, dialog.result_text, replace=True)
    except ShortcutError as error:
        context.notify("Não foi possível usar esse atalho", error.message, "error")
        return False
    return True


class ShortcutKeyButton(QPushButton):
    """Shows the current combination ("Ctrl + Shift + L" / "Desativado"); click to change it."""

    def __init__(self, context: AppContext, action_id: str) -> None:
        super().__init__()
        self._ctx = context
        self.action_id = action_id
        self.setObjectName("ShortcutKey")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: edit_shortcut(self.window(), context, action_id))
        context.shortcuts.add_listener(self._on_changed)
        self.destroyed.connect(lambda *_: context.shortcuts.remove_listener(self._on_changed))
        self.refresh()

    def _on_changed(self, _changed: set[str]) -> None:
        try:
            self.refresh()
        except RuntimeError:  # already deleted
            pass

    def refresh(self) -> None:
        manager = self._ctx.shortcuts
        label = manager.display(self.action_id)
        self.setText(label)
        self.setProperty("disabledKey", manager.combo(self.action_id) is None)
        self.setAccessibleName(f"{manager.action(self.action_id).label}: {label}. Clique para alterar.")
        self.setToolTip("Clique para alterar o atalho")
        repolish(self)


class ShortcutRow(QWidget):
    def __init__(self, context: AppContext, action_id: str) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        manager = context.shortcuts
        action = manager.action(action_id)
        self._ctx = context
        self.action_id = action_id
        self.scope = Chip(action.scope_label, "accent" if action.is_global else "neutral")
        self.scope.setToolTip("Funciona mesmo com o Luut em segundo plano" if action.is_global
                              else "Funciona com a janela em foco")
        self.unavailable = Chip("INDISPONÍVEL", "error")
        self.unavailable.setToolTip("Atalhos globais ainda não estão disponíveis no macOS. Use-o com a janela do "
                                    "Luut em foco." if SYSTEM_NAME == "macOS" else
                                    "O Windows recusou essa combinação — provavelmente outro programa já a usa. "
                                    "Escolha outra.")
        self.key = ShortcutKeyButton(context, action_id)
        self.reset = icon_button("rotate-ccw", "Restaurar o padrão deste atalho")
        self.reset.setFixedSize(30, 30)
        self.reset.clicked.connect(self._reset)
        self.setLayout(hbox(make_label(action.label), None, self.unavailable, self.scope, self.key, self.reset,
                            spacing=8))
        manager.add_listener(self._on_changed)
        self.destroyed.connect(lambda *_: manager.remove_listener(self._on_changed))
        self.refresh()

    def _on_changed(self, _changed: set[str]) -> None:
        try:
            self.refresh()
        except RuntimeError:
            pass

    def refresh(self) -> None:
        manager = self._ctx.shortcuts
        self.reset.setVisible(not manager.is_default(self.action_id))
        self.unavailable.setVisible(manager.action(self.action_id).is_global
                                    and manager.combo(self.action_id) is not None
                                    and manager.global_status(self.action_id) is False)

    def _reset(self) -> None:
        manager = self._ctx.shortcuts
        try:
            manager.reset(self.action_id)
        except ShortcutConflict as conflict:
            if ask(self.window(), "Atalho em conflito", f"{conflict.message}\n\nDeseja substituir?",
                   [("cancel", "Cancelar", None), ("replace", "Substituir", "primary")]) == "replace":
                manager.reset(self.action_id, replace=True)


class ShortcutsPanel(QWidget):
    intro_dismissed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        self._ctx = context
        self.rows: dict[str, ShortcutRow] = {}
        self.intro = self._build_intro()
        header = Card()
        header.setLayout(vbox(
            make_label("Seus atalhos", "SectionTitle"),
            make_label("Configure rapidamente os comandos do Luut. Clique em um atalho para alterá-lo. "
                       "LOCAL funciona com a janela em foco; GLOBAL funciona mesmo com o Luut em segundo plano.",
                       "Muted", wrap=True), spacing=4, margins=(22, 16, 22, 16)))
        layout = vbox(self.intro, header, spacing=18)
        self.sections: dict[str, Card] = {}
        for group in GROUP_ORDER:
            card = Card()
            section = vbox(make_label(GROUPS[group].upper(), "SectionTitle"),
                           make_label(GROUP_HINTS[group], "Muted", wrap=True), 6, spacing=8,
                           margins=(22, 18, 22, 18))
            for index, action in enumerate(context.shortcuts.actions(group)):
                if index:
                    section.addWidget(Separator())
                row = ShortcutRow(context, action.id)
                self.rows[action.id] = row
                section.addWidget(row)
            card.setLayout(section)
            self.sections[group] = card
            layout.addWidget(card)
        restore = make_button("RESTAURAR PADRÕES", "danger", "rotate-ccw")
        restore.clicked.connect(self.restore_defaults)
        self.restore_button = restore
        layout.addLayout(hbox(None, restore))
        self.setLayout(layout)

    def _build_intro(self) -> Card:
        card = Card()
        card.setObjectName("Banner")
        icon = make_label()
        icon.setPixmap(icons.pixmap("keyboard", theme.palette().accent_hover, 28))
        ok = make_button("Entendi", "primary")
        ok.clicked.connect(self.dismiss_intro)
        text = vbox(make_label("Controle o Luut pelo teclado", "SectionTitle"),
                    make_label("Personalize os comandos usados com mais frequência.<br><br>Você pode configurar "
                               "atalhos separados para:<br>• Aplicação Desktop<br>• Widget<br>• Downloads<br>"
                               "• Sistema", "Secondary", wrap=True), spacing=4)
        card.setLayout(hbox(icon, text, ok, spacing=14, margins=(18, 14, 14, 14)))
        card.layout().setAlignment(icon, Qt.AlignmentFlag.AlignTop)
        card.layout().setAlignment(ok, Qt.AlignmentFlag.AlignBottom)
        card.layout().setStretch(1, 1)
        card.setVisible(not self._ctx.shortcuts_intro_state.load().completed)
        return card

    def dismiss_intro(self) -> None:
        self._ctx.shortcuts_intro_state.mark_completed()
        self.intro.hide()
        self.intro_dismissed.emit()

    def restore_defaults(self) -> None:
        if ask(self.window(), "Restaurar atalhos?",
               "Todos os atalhos personalizados serão substituídos pelos padrões.",
               [("cancel", "Cancelar", None), ("restore", "Restaurar", "danger")]) != "restore":
            return
        self._ctx.shortcuts.restore_defaults()
        self._ctx.notify("Atalhos restaurados", "Os atalhos voltaram aos padrões.", "success")
