"""Short guided tour of the floating widget (offered the first time it is enabled).

The widget is small, so instead of covering it with an overlay the tour shows a floating card next to it and outlines
the current target. Progress is stored in tutorial_state ("widget").
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QWidget

from app.core.services.tutorial import (
    WIDGET_TOUR_STEPS,
    TutorialStateRepository,
    ordered_steps,
)
from app.infrastructure.logger import get_logger
from app.ui import icons, theme
from app.ui.floating_widget import SHADOW, paint_glass
from app.ui.widgets.common import hbox, make_button, make_label, repolish, vbox

log = get_logger("ui.widget_tour")
CARD_WIDTH = 320
GAP = 10


class WidgetTourCard(QWidget):
    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(CARD_WIDTH + 2 * SHADOW)
        self.setWindowTitle("Conheça o Widget do Luut")
        self.icon = make_label()
        self.title = make_label("", "SectionTitle", wrap=True)
        self.text = make_label("", "Secondary", wrap=True)
        self.counter = make_label("", "Muted")
        self.back = make_button("Voltar", "ghost")
        self.back.clicked.connect(self.back_clicked.emit)
        self.skip = make_button("Pular", "ghost")
        self.skip.clicked.connect(self.skip_clicked.emit)
        self.next = make_button("Próximo", "primary")
        self.next.clicked.connect(self.next_clicked.emit)
        self.setLayout(vbox(hbox(self.icon, self.title, spacing=8), self.text, 4,
                            hbox(self.counter, None, self.skip, self.back, self.next, spacing=6),
                            spacing=8, margins=(SHADOW + 16, SHADOW + 14, SHADOW + 16, SHADOW + 14)))
        self.layout().itemAt(0).layout().setStretch(1, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        paint_glass(self, QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.skip_clicked.emit()
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.next_clicked.emit()
        elif event.key() == Qt.Key.Key_Left:
            self.back_clicked.emit()
        else:
            super().keyPressEvent(event)


class WidgetTour(QObject):
    finished = Signal(bool)

    def __init__(self, host: QWidget, targets: dict[str, QWidget], state: TutorialStateRepository) -> None:
        super().__init__(host)
        self._host = host
        self._targets = targets
        self._state = state
        self.steps = ordered_steps(WIDGET_TOUR_STEPS)
        self.index = 0
        self.card = WidgetTourCard()
        self.card.next_clicked.connect(self.next)
        self.card.back_clicked.connect(self.back)
        self.card.skip_clicked.connect(lambda: self.close(completed=False))
        self._outlined: QWidget | None = None

    @property
    def running(self) -> bool:
        return self.card.isVisible()

    @property
    def current_id(self) -> str:
        return self.steps[self.index].id

    def start(self) -> None:
        log.info("Widget tour started")
        self.index = 0
        self._show()

    def next(self) -> None:
        if self.index >= len(self.steps) - 1:
            self.close(completed=True)
            return
        self.index += 1
        self._state.update(last_step=self.current_id)
        self._show()

    def back(self) -> None:
        if self.index:
            self.index -= 1
            self._show()

    def close(self, completed: bool) -> None:
        self._outline(None)
        self.card.hide()
        if completed:
            self._state.mark_completed()
        else:
            self._state.update(dont_show=True, last_step=self.current_id)  # never offered again automatically
        log.info("Widget tour %s", "completed" if completed else "skipped")
        self.finished.emit(completed)

    def _show(self) -> None:
        step = self.steps[self.index]
        palette = theme.palette()
        self.card.icon.setPixmap(icons.pixmap(step.icon, palette.accent_hover, 20))
        self.card.title.setText(step.title)
        self.card.text.setText(step.description)
        self.card.counter.setText(f"{self.index + 1} de {len(self.steps)}")
        self.card.back.setEnabled(self.index > 0)
        self.card.skip.setVisible(step.skippable and self.index < len(self.steps) - 1)
        self.card.next.setText("Concluir" if self.index == len(self.steps) - 1 else "Próximo")
        target = self._targets.get(step.target or "")
        self._outline(target if target is not self._host else None)
        self.card.adjustSize()
        self.card.move(self._card_position())
        self.card.show()
        self.card.raise_()
        self.card.activateWindow()
        self.card.next.setFocus()

    def _outline(self, widget: QWidget | None) -> None:
        if self._outlined is not None:
            self._outlined.setProperty("tourTarget", False)
            repolish(self._outlined)
        self._outlined = widget
        if widget is not None:
            widget.setProperty("tourTarget", True)
            repolish(widget)

    def _card_position(self) -> QPoint:
        host = self._host.frameGeometry()
        size = self.card.size()
        screen = QGuiApplication.screenAt(host.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        candidates = [
            QPoint(host.left() - size.width() + SHADOW - GAP, host.top()),     # left of the widget
            QPoint(host.right() - SHADOW + GAP, host.top()),                   # right
            QPoint(host.left(), host.top() - size.height() + SHADOW - GAP),    # above
            QPoint(host.left(), host.bottom() - SHADOW + GAP),                 # below
        ]
        for point in candidates:
            if area.contains(QRect(point, size)):
                return point
        x = min(max(area.left(), candidates[0].x()), area.right() - size.width())
        y = min(max(area.top(), host.top()), area.bottom() - size.height())
        return QPoint(x, y)
