"""Render assets/images/logo.svg into app.png and a multi-size app.ico (PNG-compressed entries)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "assets" / "images"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def write_ico(entries: list[tuple[int, bytes]], target: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    for size, png in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    target.write_bytes(header + directory + b"".join(png for _, png in entries))


def main() -> int:
    QGuiApplication(sys.argv[:1])
    renderer = QSvgRenderer(str(IMAGES / "logo.svg"))
    if not renderer.isValid():
        print("logo.svg is invalid")
        return 1
    entries = [(size, render_png(renderer, size)) for size in SIZES]
    write_ico(entries, IMAGES / "app.ico")
    (IMAGES / "app.png").write_bytes(entries[-1][1])
    print(f"Generated {IMAGES / 'app.ico'} ({len(entries)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
