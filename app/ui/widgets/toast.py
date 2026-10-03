"""Discreet in-app notifications that fade out on their own."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QWidget
from shiboken6 import isValid

from app.ui import icons, theme
from app.ui.widgets.common import hbox, icon_button, make_button, make_label, vbox

ToastAction = tuple[str, Callable[[], None]]

_TONE_ICONS = {"success": "check-circle", "error": "alert-circle", "warning": "alert-triangle", "info": "info"}
TOAST_WIDTH = 340
MARGIN = 20


class Toast(QFrame):
    def __init__(self, parent: QWidget, title: str, message: str, tone: str, duration_ms: int,
                 action: ToastAction | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(TOAST_WIDTH)
        palette = theme.palette()
        color = {"success": palette.success, "error": palette.error, "warning": palette.warning}.get(tone, palette.accent)
        badge = QLabel()
        badge.setPixmap(icons.pixmap(_TONE_ICONS.get(tone, "info"), color, 20))
        badge.setAlignment(Qt.AlignmentFlag.AlignTop)
        close = icon_button("x", "Fechar notificação")
        close.setFixedSize(26, 26)
        close.clicked.connect(self.dismiss)
        text = vbox(make_label(title, "ToastTitle"), make_label(message, "Secondary", wrap=True), spacing=2)
        if action is not None:
            label, callback = action
            button = make_button(label, "link")
            button.clicked.connect(lambda: (callback(), self.dismiss()))
            text.addLayout(hbox(button, None))
        self.setLayout(hbox(badge, text, close, spacing=12, margins=(14, 12, 10, 12)))
        self.layout().setAlignment(badge, Qt.AlignmentFlag.AlignTop)
        self.layout().setAlignment(close, Qt.AlignmentFlag.AlignTop)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        QTimer.singleShot(duration_ms, self.dismiss)
        self._closing = False

    def appear(self) -> None:
        self.show()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.deleteLater)
        self._fade.start()


class ToastManager(QObject):
    """Stacks toasts in the bottom-right corner of the host widget."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(self, title: str, message: str, tone: str = "info", duration_ms: int = 4500,
             action: ToastAction | None = None) -> None:
        toast = Toast(self._host, title, message, tone, duration_ms if action is None else duration_ms + 3000, action)
        toast.destroyed.connect(lambda *_: self._forget(toast))
        self._toasts.append(toast)
        if len(self._toasts) > 4:
            self._toasts[0].dismiss()
        toast.adjustSize()
        toast.appear()
        toast.raise_()
        self._relayout()

    def _forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        if isValid(self._host):  # the window may already be gone at shutdown
            self._relayout()

    def _relayout(self) -> None:
        y = self._host.height() - MARGIN
        for toast in reversed(self._toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(self._host.width() - toast.width() - MARGIN, y)
            y -= 10

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self._relayout()
        return False
