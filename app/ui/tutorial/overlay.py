"""Spotlight overlay: dims the window, highlights the target widget and shows the step bubble."""

from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QObject, QPropertyAnimation, QRect, QRectF, QSize, Qt, Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QWidget

from app.core.services.tutorial import Placement, TutorialStep
from app.ui import icons, theme
from app.ui.icons import to_qcolor
from app.ui.widgets.common import hbox, icon_button, make_button, make_label, vbox

HOLE_PADDING = 8
HOLE_RADIUS = 12
BUBBLE_WIDTH = 380
CENTER_WIDTH = 460
EDGE_MARGIN = 16
GAP = 14


class _Dots(QWidget):
    """Progress indicator: ● ● ● ○ ○."""

    def __init__(self) -> None:
        super().__init__()
        self._count = 1
        self._current = 0
        self.setFixedHeight(10)

    def set_progress(self, current: int, count: int) -> None:
        self._current, self._count = current, count
        self.setFixedWidth(count * 12)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(self._count):
            painter.setBrush(to_qcolor(palette.accent if index <= self._current else palette.border_strong))
            painter.drawEllipse(QRectF(index * 12 + 1, 1, 8, 8))
        painter.end()


class TutorialBubble(QFrame):
    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()
    action_clicked = Signal(str)
    dont_show_toggled = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TutorialBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._action: str | None = None

        self.badge = QLabel()
        self.counter = make_label("", "Muted")
        self.close_button = icon_button("x", "Fechar tutorial (Esc)")
        self.close_button.clicked.connect(self.skip_clicked.emit)
        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = make_label("", "SectionTitle", wrap=True)
        self.body = make_label("", "Secondary", wrap=True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.action_button = make_button("", None, "folder")
        self.action_button.clicked.connect(lambda: self.action_clicked.emit(self._action or ""))
        self.dots = _Dots()
        self.dont_show = QCheckBox("Não mostrar este tutorial novamente")
        self.dont_show.toggled.connect(self.dont_show_toggled.emit)
        self.skip_button = make_button("Pular", "ghost")
        self.skip_button.clicked.connect(self.skip_clicked.emit)
        self.back_button = make_button("Voltar", None, "chevron-left")
        self.back_button.clicked.connect(self.back_clicked.emit)
        self.next_button = make_button("Próximo", "primary", "chevron-right")
        self.next_button.clicked.connect(self.next_clicked.emit)
        self.next_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        self.setLayout(vbox(
            hbox(self.badge, self.counter, None, self.close_button, spacing=8),
            self.logo, self.title, self.body, hbox(self.action_button, None), 4,
            hbox(self.dots, None), self.dont_show, 4,
            hbox(self.skip_button, None, self.back_button, self.next_button, spacing=8),
            spacing=8, margins=(18, 14, 18, 16)))

    def set_step(self, step: TutorialStep, index: int, count: int, dont_show: bool) -> None:
        palette = theme.palette()
        welcome = step.id == "welcome"
        centered = step.placement == Placement.CENTER
        self.setFixedWidth(CENTER_WIDTH if centered else BUBBLE_WIDTH)
        self.badge.setPixmap(icons.pixmap(step.icon, palette.accent_hover, 18))
        self.counter.setText(f"Etapa {index + 1} de {count}")
        self.logo.setVisible(centered)
        if centered:
            self.logo.setPixmap(icons.logo_pixmap(72) if welcome else icons.pixmap(step.icon, palette.accent_hover, 48))
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter if centered else Qt.AlignmentFlag.AlignLeft)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter if centered else Qt.AlignmentFlag.AlignLeft)
        self.title.setText(step.title)
        self.body.setText(step.description)
        self._action = step.action
        self.action_button.setVisible(bool(step.action))
        self.action_button.setText(step.action_label or "")
        self.dots.set_progress(index, count)
        self.dont_show.blockSignals(True)
        self.dont_show.setChecked(dont_show)
        self.dont_show.blockSignals(False)
        self.dont_show.setVisible(step.id != "done")
        last = index == count - 1
        self.back_button.setVisible(index > 0 and not welcome)
        self.skip_button.setVisible(step.skippable)
        self.skip_button.setText("Pular tutorial" if welcome else "Pular")
        self.next_button.setText("Começar tutorial" if welcome else "Começar a usar" if last else "Próximo")
        self.next_button.setToolTip("Enter / →")
        self.adjustSize()
        self.next_button.setFocus()


class TutorialOverlay(QWidget):
    """Covers the host widget. Blocks interaction with the app while the tutorial is open."""

    next_requested = Signal()
    back_requested = Signal()
    skip_requested = Signal()
    host_resized = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._hole = QRectF()
        self._has_hole = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.bubble = TutorialBubble(self)
        self.bubble.next_clicked.connect(self.next_requested.emit)
        self.bubble.back_clicked.connect(self.back_requested.emit)
        self.bubble.skip_clicked.connect(self.skip_requested.emit)
        self._animation = QPropertyAnimation(self, b"hole", self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        host.installEventFilter(self)
        self.setGeometry(host.rect())

    # animated spotlight rectangle
    def _get_hole(self) -> QRectF:
        return self._hole

    def _set_hole(self, rect: QRectF) -> None:
        self._hole = rect
        self._place_bubble()
        self.update()

    hole = Property(QRectF, _get_hole, _set_hole)

    def show_step(self, step: TutorialStep, index: int, count: int, dont_show: bool, target: QRect | None) -> None:
        self.bubble.set_step(step, index, count, dont_show)
        self._placement = step.placement
        self._animation.stop()
        if target is None or target.isEmpty():
            self._has_hole = False
            self._hole = QRectF()
            self._place_bubble()
            self.update()
        else:
            padded = QRectF(target.adjusted(-HOLE_PADDING, -HOLE_PADDING, HOLE_PADDING, HOLE_PADDING))
            padded = padded.intersected(QRectF(self.rect()).adjusted(2, 2, -2, -2))
            if self._has_hole and not self._hole.isEmpty():
                self._animation.setStartValue(self._hole)
                self._animation.setEndValue(padded)
                self._animation.start()
            else:
                self._has_hole = True
                self._set_hole(padded)
        self.show()
        self.raise_()
        self.bubble.raise_()
        self.setFocus()

    # --------------------------------------------------------------- geometry
    def _place_bubble(self) -> None:
        size = self.bubble.sizeHint().expandedTo(QSize(self.bubble.width(), 0))
        self.bubble.resize(size)
        bounds = QRect(self.rect()).adjusted(EDGE_MARGIN, EDGE_MARGIN, -EDGE_MARGIN, -EDGE_MARGIN)
        if not self._has_hole:
            rect = QRect(0, 0, size.width(), size.height())
            rect.moveCenter(bounds.center())
        else:
            rect = self._best_rect(size, bounds)
        self.bubble.setGeometry(self._clamp(rect, bounds))

    def _candidates(self) -> list[Placement]:
        preferred = getattr(self, "_placement", Placement.BOTTOM)
        order = [Placement.BOTTOM, Placement.TOP, Placement.RIGHT, Placement.LEFT]
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        return order

    def _best_rect(self, size: QSize, bounds: QRect) -> QRect:
        hole = self._hole.toAlignedRect()
        width, height = size.width(), size.height()
        for placement in self._candidates():
            if placement == Placement.BOTTOM:
                rect = QRect(hole.center().x() - width // 2, hole.bottom() + GAP, width, height)
            elif placement == Placement.TOP:
                rect = QRect(hole.center().x() - width // 2, hole.top() - GAP - height, width, height)
            elif placement == Placement.RIGHT:
                rect = QRect(hole.right() + GAP, hole.center().y() - height // 2, width, height)
            else:
                rect = QRect(hole.left() - GAP - width, hole.center().y() - height // 2, width, height)
            clamped = self._clamp(rect, bounds)
            fits_axis = (bounds.top() <= rect.top() and rect.bottom() <= bounds.bottom()) if placement in (
                Placement.BOTTOM, Placement.TOP) else (bounds.left() <= rect.left() and rect.right() <= bounds.right())
            if fits_axis and not clamped.intersects(hole):
                return clamped
        # Nothing fits without overlapping (very large target): keep the bubble inside the window.
        rect = QRect(bounds.right() - width, bounds.bottom() - height, width, height)
        return rect

    @staticmethod
    def _clamp(rect: QRect, bounds: QRect) -> QRect:
        x = min(max(rect.left(), bounds.left()), max(bounds.left(), bounds.right() - rect.width()))
        y = min(max(rect.top(), bounds.top()), max(bounds.top(), bounds.bottom() - rect.height()))
        return QRect(x, y, rect.width(), rect.height())

    # ------------------------------------------------------------------ events
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
            self.host_resized.emit()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        palette = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dim = QColor(to_qcolor(palette.shadow))
        dim.setAlpha(150 if palette.name == "dark" else 110)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if self._has_hole and not self._hole.isEmpty():
            spot = QPainterPath()
            spot.addRoundedRect(self._hole, HOLE_RADIUS, HOLE_RADIUS)
            path = path.subtracted(spot)
        painter.fillPath(path, dim)
        if self._has_hole and not self._hole.isEmpty():
            painter.setPen(QPen(to_qcolor(palette.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self._hole, HOLE_RADIUS, HOLE_RADIUS)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()  # the app underneath stays untouched while the tutorial is open

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.skip_requested.emit()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.next_requested.emit()
        elif key == Qt.Key.Key_Left:
            self.back_requested.emit()
        else:
            super().keyPressEvent(event)
