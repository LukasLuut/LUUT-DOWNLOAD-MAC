"""System tray icon and menu."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app import APP_NAME
from app.ui import icons
from app.ui.context import AppContext


class TrayController(QObject):
    open_requested = Signal()
    quit_requested = Signal()
    page_requested = Signal(str)
    show_widget_requested = Signal()
    hide_widget_requested = Signal()

    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = context
        self._message_action: Callable[[], None] | None = None
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        title = menu.addAction(APP_NAME.upper())
        title.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Abrir Luut", self.open_requested.emit)
        self.show_widget_action = menu.addAction("Mostrar Widget", self.show_widget_requested.emit)
        self.hide_widget_action = menu.addAction("Ocultar Widget", self.hide_widget_requested.emit)
        menu.addSeparator()
        menu.addAction("Pausar todos", context.downloads.pause_all)
        menu.addAction("Retomar todos", context.downloads.resume_all)
        menu.addSeparator()
        menu.addAction("Abrir Downloads", lambda: self.page_requested.emit("downloads"))
        menu.addAction("Abrir Histórico", lambda: self.page_requested.emit("history"))
        menu.addAction("Configurações", lambda: self.page_requested.emit("settings"))
        menu.addSeparator()
        menu.addAction("Sair", self.quit_requested.emit)
        self._menu = menu
        self.set_widget_state(False, False)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self._message_clicked)
        if self.available:
            self.tray.show()

    @property
    def menu(self) -> QMenu:
        return self._menu

    def set_widget_state(self, enabled: bool, visible: bool) -> None:
        self.show_widget_action.setVisible(enabled)
        self.hide_widget_action.setVisible(enabled)
        self.show_widget_action.setEnabled(enabled and not visible)
        self.hide_widget_action.setEnabled(enabled and visible)

    def show_menu(self) -> bool:
        if not self.available:
            return False
        self._menu.popup(QCursor.pos())
        self._menu.activateWindow()
        return True

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()

    def _message_clicked(self) -> None:
        action, self._message_action = self._message_action, None
        if action is not None:
            action()  # e.g. "Abrir arquivo" of a completed download (an explicit click by the user)
        else:
            self.open_requested.emit()

    def set_active_count(self, count: int) -> None:
        self.tray.setToolTip(f"{APP_NAME} — {count} download(s) em andamento" if count else APP_NAME)

    def notify(self, title: str, message: str, error: bool = False,
               on_click: Callable[[], None] | None = None) -> bool:
        if not self.available or not self.tray.isVisible():
            return False
        self._message_action = on_click
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, message, icon, 6000)
        return True

    def hide(self) -> None:
        self.tray.hide()
