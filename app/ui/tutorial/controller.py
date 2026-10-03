"""Drives the tutorial over the real interface: navigation, targets, demo data and persistence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Signal
from PySide6.QtWidgets import QScrollArea, QWidget

from app.core.services.tutorial import TutorialManager, TutorialStep
from app.infrastructure.logger import get_logger
from app.ui.tutorial.overlay import TutorialOverlay

log = get_logger("ui.tutorial")

TargetResolver = Callable[[], QWidget | None]


class TutorialHost(Protocol):
    """What the controller needs from the window (kept small so pages stay tutorial-agnostic)."""

    overlay_host: QWidget
    targets: dict[str, TargetResolver]

    def navigate(self, page: str) -> None: ...

    def set_demo(self, enabled: bool) -> None: ...

    def run_tutorial_action(self, action: str) -> None: ...


class TutorialController(QObject):
    finished = Signal(bool)  # completed?

    def __init__(self, host: TutorialHost, manager: TutorialManager) -> None:
        super().__init__()
        self._host = host
        self._manager = manager
        self._overlay: TutorialOverlay | None = None

    @property
    def running(self) -> bool:
        return self._overlay is not None

    @property
    def overlay(self) -> TutorialOverlay | None:
        return self._overlay

    def start(self) -> None:
        if self._overlay is not None:
            return
        log.info("Tutorial started")
        overlay = TutorialOverlay(self._host.overlay_host)
        overlay.next_requested.connect(self.next)
        overlay.back_requested.connect(self.back)
        overlay.skip_requested.connect(self.skip)
        overlay.host_resized.connect(self.refresh)
        overlay.bubble.dont_show_toggled.connect(self._manager.set_dont_show)
        overlay.bubble.action_clicked.connect(self._host.run_tutorial_action)
        self._overlay = overlay
        self._show(self._manager.start())

    def next(self) -> None:
        step = self._manager.next()
        if step is None:
            self._close(completed=True)
        else:
            self._show(step)

    def back(self) -> None:
        if not self._manager.is_first:
            self._show(self._manager.back())

    def skip(self) -> None:
        if self._overlay is not None:
            self._manager.finish(completed=False)
            self._close(completed=False)

    def refresh(self) -> None:
        if self._overlay is not None:
            self._display(self._manager.current)

    # ----------------------------------------------------------------- internal
    def _show(self, step: TutorialStep) -> None:
        if step.page:
            self._host.navigate(step.page)
        self._host.set_demo(step.demo)
        # Let the page switch and layouts settle before measuring the target.
        QTimer.singleShot(40, lambda: self._display(step))

    def _display(self, step: TutorialStep) -> None:
        if self._overlay is None or self._manager.current is not step:
            return
        target = self._target_rect(step)
        self._overlay.show_step(step, self._manager.index, len(self._manager.steps),
                                self._manager.state.dont_show, target)

    def _target_rect(self, step: TutorialStep) -> QRect | None:
        if not step.target or self._overlay is None:
            return None
        resolver = self._host.targets.get(step.target)
        widget = resolver() if resolver else None
        if widget is None or not widget.isVisible():
            log.warning("Tutorial target not available: %s", step.target)
            return None
        self._ensure_visible(widget)
        top_left = widget.mapTo(self._overlay.parentWidget(), widget.rect().topLeft())
        rect = QRect(top_left, widget.size())
        return rect.intersected(self._visible_area(widget))

    @staticmethod
    def _scroll_area(widget: QWidget) -> QScrollArea | None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _ensure_visible(self, widget: QWidget) -> None:
        """Scroll so the target sits near the top of the page, leaving room for the bubble below it."""
        area = self._scroll_area(widget)
        if area is not None and area.widget() is not None:
            top = widget.mapTo(area.widget(), QPoint(0, 0)).y()
            area.verticalScrollBar().setValue(max(0, top - 24))

    def _visible_area(self, widget: QWidget) -> QRect:
        assert self._overlay is not None
        host = self._overlay.parentWidget()
        area = self._scroll_area(widget)
        if area is None:
            return host.rect()
        viewport = area.viewport()
        return QRect(viewport.mapTo(host, viewport.rect().topLeft()), viewport.size())

    def _close(self, completed: bool) -> None:
        log.info("Tutorial %s", "completed" if completed else "skipped")
        self._host.set_demo(False)
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None
        self._host.navigate("home")
        self.finished.emit(completed)
