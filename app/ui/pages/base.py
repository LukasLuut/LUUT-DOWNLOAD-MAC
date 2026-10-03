"""Page scaffold: scrollable, centered content column with a title header."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.ui.widgets.common import make_label

CONTENT_MAX_WIDTH = 1180


class Page(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll

        holder = QWidget()
        holder.setObjectName("Transparent")
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(36, 28, 36, 36)
        column = QWidget()
        column.setObjectName("Transparent")
        column.setMaximumWidth(CONTENT_MAX_WIDTH)
        outer.addWidget(column)

        self.content = QVBoxLayout(column)
        self.content.setContentsMargins(0, 0, 0, 0)
        self.content.setSpacing(18)
        self.title_label = make_label(title, "PageTitle")
        self.title_label.setAccessibleName(title)
        self.subtitle_label = make_label(subtitle, "PageSubtitle", wrap=True)
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(self.title_label)
        if subtitle:
            header.addWidget(self.subtitle_label)
        self.header = QHBoxLayout()
        self.header.addLayout(header, 1)
        self.content.addLayout(self.header)
        self.content.addSpacing(4)

        scroll.setWidget(holder)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def finish(self) -> None:
        """Push content to the top."""
        self.content.addStretch(1)

    def on_shown(self) -> None:
        """Hook: called when the page becomes visible."""


class ResponsiveRow(QWidget):
    """Lays children side by side, stacking them vertically below `breakpoint` px."""

    def __init__(self, breakpoint: int, spacing: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Transparent")
        self._breakpoint = breakpoint
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(spacing)

    def add(self, widget: QWidget, stretch: int = 1) -> None:
        self._layout.addWidget(widget, stretch)

    def resizeEvent(self, event) -> None:  # noqa: N802
        direction = (QBoxLayout.Direction.LeftToRight if event.size().width() >= self._breakpoint
                     else QBoxLayout.Direction.TopToBottom)
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
        super().resizeEvent(event)
