"""SVG icon loading, tinted with theme colors."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.ui import theme
from app.utils.paths import app_data_dir, resource_path

_RENDER_SCALE = 2  # render at 2x for crisp icons on HiDPI screens


def to_qcolor(value: str) -> QColor:
    raw = value.lstrip("#")
    if len(raw) == 8:
        color = QColor(f"#{raw[:6]}")
        color.setAlpha(int(raw[6:], 16))
        return color
    return QColor(value)


@lru_cache(maxsize=64)
def _svg_source(name: str) -> str:
    path = resource_path("assets", "icons", f"{name}.svg")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    source = _svg_source(name).replace("currentColor", color[:7])
    renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
    image = QImage(size * _RENDER_SCALE, size * _RENDER_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if renderer.isValid():
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
        painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(_RENDER_SCALE)
    return result


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    palette = theme.palette()
    result = QIcon()
    result.addPixmap(pixmap(name, color or palette.text_secondary, size), QIcon.Mode.Normal)
    result.addPixmap(pixmap(name, palette.text_muted, size), QIcon.Mode.Disabled)
    return result


def app_icon() -> QIcon:
    return QIcon(str(resource_path("assets", "images", "app.ico")))


@lru_cache(maxsize=8)
def logo_pixmap(size: int) -> QPixmap:
    renderer = QSvgRenderer(str(resource_path("assets", "images", "logo.svg")))
    image = QImage(size * _RENDER_SCALE, size * _RENDER_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(_RENDER_SCALE)
    return result


def icon_file(name: str, color: str) -> str:
    """Write a tinted copy of an SVG icon to the cache and return its path (for stylesheets)."""
    folder = app_data_dir() / "cache" / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}-{color.lstrip('#')[:6]}.svg"
    if not target.exists():
        target.write_text(_svg_source(name).replace("currentColor", color[:7]), encoding="utf-8")
    return target.as_posix()


def stylesheet(palette: theme.Palette) -> str:
    return theme.build_stylesheet(palette, combo_arrow=icon_file("chevron-down", palette.text_secondary))
