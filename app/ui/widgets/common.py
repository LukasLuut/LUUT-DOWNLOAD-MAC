"""Small reusable building blocks shared by pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from app.ui import icons, theme


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def make_label(text: str = "", name: str | None = None, wrap: bool = False,
               selectable: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def make_button(text: str = "", variant: str | None = None, icon_name: str | None = None,
                tooltip: str | None = None, icon_color: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if variant:
        button.setProperty("variant", variant)
    if icon_name:
        palette = theme.palette()
        color = icon_color or (palette.on_accent if variant == "primary" else palette.text_secondary)
        button.setIcon(icons.icon(icon_name, color))
        button.setIconSize(QSize(16, 16))
        button.setProperty("icon_name", icon_name)
    if tooltip:
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
    elif text:
        button.setAccessibleName(text)
    return button


def icon_button(icon_name: str, tooltip: str) -> QPushButton:
    button = make_button("", "icon", icon_name, tooltip)
    button.setFixedSize(34, 34)
    button.setIconSize(QSize(18, 18))
    return button


def refresh_button_icons(root: QWidget) -> None:
    """Re-tint icons after a theme change."""
    palette = theme.palette()
    for button in root.findChildren(QPushButton):
        name = button.property("icon_name")
        if name:
            color = palette.on_accent if button.property("variant") == "primary" else palette.text_secondary
            button.setIcon(icons.icon(name, color))


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, shadow: bool = False, alt: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(28)
            effect.setOffset(0, 6)
            color = QColor(theme.palette().shadow)
            color.setAlpha(60)
            effect.setColor(color)
            self.setGraphicsEffect(effect)


class Chip(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.set_tone(tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class Separator(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Separator")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def rounded_pixmap(source: QPixmap, width: int, height: int, radius: int) -> QPixmap:
    """Center-crop `source` to width x height with rounded corners (HiDPI aware)."""
    ratio = 2
    target = QPixmap(width * ratio, height * ratio)
    target.fill(Qt.GlobalColor.transparent)
    scaled = source.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, target.width(), target.height(), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawPixmap((target.width() - scaled.width()) // 2, (target.height() - scaled.height()) // 2, scaled)
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


class Thumbnail(QLabel):
    """Rounded thumbnail with a film placeholder when no image is available."""

    def __init__(self, width: int, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Thumb")
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._data: bytes | None = None
        self.set_image(None)

    def set_image(self, data: bytes | None) -> None:
        self._data = data
        source = QPixmap()
        if data and source.loadFromData(data):
            self.setPixmap(rounded_pixmap(source, self.width(), self.height(), theme.RADIUS_SMALL))
            return
        size = max(18, min(self.width(), self.height()) // 3)
        self.setPixmap(icons.pixmap("film", theme.palette().text_muted, size))


def hbox(*items: QWidget | QLayout | int | None, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    _fill(layout, items, spacing, margins)
    return layout


def vbox(*items: QWidget | QLayout | int | None, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    _fill(layout, items, spacing, margins)
    return layout


def _fill(layout: QHBoxLayout | QVBoxLayout, items, spacing: int, margins: tuple[int, int, int, int]) -> None:
    """Add widgets/layouts; an int adds that much fixed space; None adds a stretch."""
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for item in items:
        if item is None:
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)


class ElidedLabel(QLabel):
    """Single-line label that elides long text with "…" and shows the full text as tooltip."""

    def __init__(self, text: str = "", name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))
