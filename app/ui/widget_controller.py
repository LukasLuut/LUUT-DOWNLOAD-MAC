"""Lifecycle of the floating widget: enable/disable, show/hide, compact mode, position, clipboard and tour.

Disabled (the default) means nothing exists: no windows, no clipboard connection, no widget hotkeys.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid

from app.core.models.settings import AppSettings
from app.infrastructure.logger import get_logger
from app.system.clipboard_monitor import ClipboardMonitor
from app.ui.context import AppContext
from app.ui.floating_widget import (
    CompactBubble,
    FloatingWidget,
    available_screens,
    clamp_to_screens,
    resolve_position,
)
from app.ui.task_actions import TaskActions
from app.ui.widget_tour import WidgetTour

log = get_logger("ui.widget")

_WINDOW_KEYS = {"widget_always_on_top", "widget_opacity"}


class WidgetController(QObject):
    visibility_changed = Signal()
    tour_offer_requested = Signal()

    def __init__(self, context: AppContext, actions: TaskActions, open_app: Callable[[str], None],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = context
        self._actions = actions
        self._open_app = open_app
        self.widget: FloatingWidget | None = None
        self.bubble: CompactBubble | None = None
        self.tour: WidgetTour | None = None
        self._compact = context.settings.settings.widget_compact
        self._session_pos: QPoint | None = None
        self.clipboard = ClipboardMonitor(parent=self)
        self.clipboard.url_detected.connect(self._on_clipboard_url)
        context.settings.add_listener(self._on_settings)
        shortcuts = context.shortcuts
        shortcuts.register_handler("widget.toggle", self.toggle)
        shortcuts.register_handler("widget.focus", self.focus)
        self._apply_clipboard(context.settings.settings)

    # ------------------------------------------------------------------ state
    @property
    def enabled(self) -> bool:
        return self._ctx.settings.settings.widget_enabled

    @property
    def is_visible(self) -> bool:
        return any(w is not None and isValid(w) and w.isVisible() for w in (self.widget, self.bubble))

    @property
    def compact(self) -> bool:
        return self._compact

    def _ensure_windows(self) -> None:
        if self.widget is not None:
            return
        self.widget = FloatingWidget(self._ctx, self._actions)
        self.widget.compact_requested.connect(self.minimize)
        self.widget.hide_requested.connect(self.hide)
        self.widget.open_app_requested.connect(self._open_app)
        self.widget.geometry_saved.connect(self._save_widget_position)
        self.bubble = CompactBubble(self._ctx)
        self.bubble.expand_requested.connect(self.expand)
        self.bubble.geometry_saved.connect(self._save_bubble_position)

    def destroy(self) -> None:
        if self.tour is not None and self.tour.running:
            self.tour.close(completed=False)
        for window in (self.widget, self.bubble):
            if window is not None and isValid(window):
                window.hide()
                window.deleteLater()
        self.widget = self.bubble = None
        self.tour = None

    # ------------------------------------------------------------- commands
    def show(self, activate: bool = True) -> bool:
        if not self.enabled:
            return False
        self._ensure_windows()
        assert self.widget is not None and self.bubble is not None
        if self._compact:
            self.widget.hide()
            self._place(self.bubble)
            self.bubble.show()
            self.bubble.raise_()
        else:
            self.bubble.hide()
            self.widget.adjustSize()
            self._place(self.widget)
            self.widget.show()
            self.widget.raise_()
            if activate:
                self.widget.activateWindow()
                self.widget.url_input.setFocus()
        log.info("Widget opened (%s)", "compact" if self._compact else "full")
        self.visibility_changed.emit()
        return True

    def hide(self) -> None:
        was_visible = self.is_visible
        for window in (self.widget, self.bubble):
            if window is not None and isValid(window) and window.isVisible():
                self._remember(window)
                window.hide()
        if was_visible:
            log.info("Widget closed")
        self.visibility_changed.emit()

    def toggle(self) -> bool:
        """Show/hide (the "Focar Widget" command brings it to the front without hiding)."""
        if not self.enabled:
            return False
        if self.is_visible:
            self.hide()
        else:
            self.show()
        return True

    def focus(self) -> bool:
        return self.show(activate=True)

    def minimize(self) -> None:
        """Full widget -> compact floating button."""
        if self.widget is None or self.bubble is None:
            return
        self._remember(self.widget)
        self._set_compact(True)
        self.widget.hide()
        self._place(self.bubble)
        self.bubble.show()
        self.visibility_changed.emit()

    def expand(self) -> None:
        if self.widget is None or self.bubble is None:
            return
        if self.bubble.isVisible():
            self._session_pos = self._widget_pos_from_bubble(self.bubble.geometry())
        self._set_compact(False)
        self.bubble.hide()
        self.show()

    def _set_compact(self, compact: bool) -> None:
        self._compact = compact
        if self._ctx.settings.settings.widget_compact != compact:
            self._ctx.settings.update(widget_compact=compact)

    # ------------------------------------------------------------ positions
    def _place(self, window) -> None:
        window.adjustSize()
        screens = available_screens()
        primary_screen = QGuiApplication.primaryScreen()
        primary = primary_screen.availableGeometry() if primary_screen else QRect(0, 0, 1280, 720)
        widget_size = self.widget.sizeHint() if self.widget is not None else window.size()
        if self._session_pos is not None:
            top_left = clamp_to_screens(QRect(self._session_pos, widget_size.expandedTo(window.size())), screens)
        else:
            top_left = resolve_position(self._ctx.settings.settings, widget_size, screens, primary)
        widget_rect = QRect(top_left, widget_size)
        if window is self.bubble:
            target = self._bubble_rect_for(widget_rect, window.size())
        else:
            target = QRect(top_left, window.size())
        window.move(clamp_to_screens(target, screens))
        if window is self.widget:
            window.update_anchor()

    @staticmethod
    def _anchors(rect: QRect) -> tuple[bool, bool]:
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else rect
        return rect.center().y() > area.center().y(), rect.center().x() > area.center().x()

    def _bubble_rect_for(self, widget_rect: QRect, size) -> QRect:
        """Put the compact button at the widget corner nearest to the screen edges."""
        bottom, right = self._anchors(widget_rect)
        x = widget_rect.right() - size.width() + 1 if right else widget_rect.left()
        y = widget_rect.bottom() - size.height() + 1 if bottom else widget_rect.top()
        return QRect(QPoint(x, y), size)

    def _widget_pos_from_bubble(self, bubble_rect: QRect) -> QPoint:
        size = self.widget.sizeHint() if self.widget is not None else bubble_rect.size()
        bottom, right = self._anchors(bubble_rect)
        x = bubble_rect.right() - size.width() + 1 if right else bubble_rect.left()
        y = bubble_rect.bottom() - size.height() + 1 if bottom else bubble_rect.top()
        return QPoint(x, y)

    def _remember(self, window) -> None:
        if window is self.widget:
            self._session_pos = window.pos()
        elif window is self.bubble:
            self._session_pos = self._widget_pos_from_bubble(window.geometry())

    def _save_widget_position(self, position: QPoint) -> None:
        self._session_pos = position
        if self.widget is not None:
            self.widget.update_anchor()
        self._persist_position(position)

    def _save_bubble_position(self, _position: QPoint) -> None:
        if self.bubble is None:
            return
        position = self._widget_pos_from_bubble(self.bubble.geometry())
        self._session_pos = position
        self._persist_position(position)

    def _persist_position(self, position: QPoint) -> None:
        self._ctx.settings.update(widget_has_position=True, widget_x=position.x(), widget_y=position.y())
        log.info("Widget moved to %d,%d", position.x(), position.y())

    # -------------------------------------------------------------- settings
    def start(self) -> None:
        """Called once the main window exists (normal start or start with Windows)."""
        if self.enabled:
            self.show(activate=False)

    def _on_settings(self, settings: AppSettings, changed: set[str]) -> None:
        if "widget_enabled" in changed:
            if settings.widget_enabled:
                log.info("Widget enabled")
                self._compact = False
                self.show()
                if self._ctx.widget_tour_state.load().should_autostart:
                    QTimer.singleShot(250, self.tour_offer_requested.emit)
            else:
                log.info("Widget disabled")
                self.hide()
                self.destroy()
        if "widget_position" in changed:
            self._session_pos = None
            if self.is_visible:
                self._place(self.bubble if self._compact else self.widget)
        if changed & _WINDOW_KEYS:
            for window in (self.widget, self.bubble):
                if window is not None and isValid(window):
                    window.setWindowOpacity(settings.widget_opacity / 100)
                    if "widget_always_on_top" in changed:
                        window.apply_window_flags(settings.widget_always_on_top)
        if changed & {"widget_enabled", "widget_detect_clipboard"}:
            self._apply_clipboard(settings)
        if "theme" in changed:
            for window in (self.widget, self.bubble):
                if window is not None and isValid(window):
                    window.refresh_theme()

    def _apply_clipboard(self, settings: AppSettings) -> None:
        self.clipboard.set_enabled(settings.widget_enabled and settings.widget_detect_clipboard)

    def _on_clipboard_url(self, url: str) -> None:
        if not self.enabled:
            return
        self._ensure_windows()
        assert self.widget is not None
        if self._compact or not self.widget.isVisible():
            self._set_compact(False)
            self.show(activate=False)
        self.widget.offer_url(url)  # the user decides: Analisar or Ignorar — never downloads by itself

    # ------------------------------------------------------------------ tour
    def start_tour(self) -> None:
        if not self.enabled:
            return
        self._set_compact(False)
        self.show()
        assert self.widget is not None
        self.tour = WidgetTour(self.widget, self.widget.tour_targets(), self._ctx.widget_tour_state)
        self.tour.start()

    def decline_tour(self) -> None:
        self._ctx.widget_tour_state.update(dont_show=True)
