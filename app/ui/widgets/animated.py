"""Custom-painted animated widgets: progress bar, spinner and toggle switch."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from app.ui import theme
from app.ui.icons import to_qcolor


class ProgressBar(QWidget):
    """Smoothly animated progress. `set_fraction(None)` shows an honest indeterminate state."""

    def __init__(self, parent: QWidget | None = None, height: int = 8) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = 0.0
        self._indeterminate = False
        self._phase = 0.0
        self._tone = "accent"
        self._animation = QPropertyAnimation(self, b"value", self)
        self._animation.setDuration(280)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _get_value(self) -> float:
        return self._value

    def _set_value(self, value: float) -> None:
        self._value = value
        self.update()

    value = Property(float, _get_value, _set_value)

    def set_fraction(self, fraction: float | None) -> None:
        if fraction is None:
            self._indeterminate = True
            if not self._timer.isActive():
                self._timer.start()
            return
        self._indeterminate = False
        self._timer.stop()
        target = max(0.0, min(1.0, fraction))
        if abs(target - self._value) < 0.001:
            return
        self._animation.stop()
        if target < self._value:
            self._set_value(target)
            return
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(target)
        self._animation.start()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.4
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        if self._indeterminate:
            self._timer.start()
        super().showEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(to_qcolor(palette.surface_hover))
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        color = to_qcolor({"success": palette.success, "error": palette.error,
                           "warning": palette.warning, "muted": palette.text_muted}.get(self._tone, palette.accent))
        painter.setBrush(color)
        width = self.width()
        if self._indeterminate:
            segment = width * 0.3
            start = (self._phase - 0.3) * width
            rect = QRectF(max(0.0, start), 0, min(segment, width - max(0.0, start)), self.height())
            if rect.width() > 0:
                painter.drawRoundedRect(rect, radius, radius)
        elif self._value > 0:
            painter.drawRoundedRect(QRectF(0, 0, max(self.height(), width * self._value), self.height()), radius, radius)
        painter.end()


class Spinner(QWidget):
    def __init__(self, size: int = 22, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._rotate)

    def _rotate(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = 3
        rect = QRectF(width, width, self.width() - 2 * width, self.height() - 2 * width)
        track = QPen(to_qcolor(palette.surface_hover), width)
        painter.setPen(track)
        painter.drawEllipse(rect)
        arc = QPen(to_qcolor(palette.accent), width)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)
        painter.end()


class ToggleSwitch(QAbstractButton):
    """Accessible on/off switch (keyboard: Space) with a sliding knob."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(44, 24)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._animation.stop()
        self._set_offset(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, 42, 22)
        off = to_qcolor(palette.border_strong)
        on = to_qcolor(palette.accent)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        if not self.isEnabled():
            track.setAlpha(110)
        painter.setPen(QPen(to_qcolor(palette.text), 1.5) if self.hasFocus() else Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, 11, 11)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(palette.on_accent))
        x = 4 + self._offset * 20
        painter.drawEllipse(QRectF(x, 4, 16, 16))
        painter.end()
