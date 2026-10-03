"""Optional detection of copied URLs (off by default).

Event-driven (QClipboard.dataChanged), never polled. Only the current plain text is read — images and other formats
are ignored — and nothing is stored, logged or sent anywhere: only the last offered URL is kept in memory so the same
link is not offered twice in a row. Detecting a URL never starts a download; the user must confirm.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QClipboard, QGuiApplication

from app.infrastructure.logger import get_logger
from app.utils.urls import MAX_URL_LENGTH, normalize_url

log = get_logger("clipboard")


def read_clipboard_url(clipboard: QClipboard | None = None) -> tuple[str, str | None]:
    """(plain text, normalized URL or None). Used by the "Colar" buttons."""
    board = clipboard or QGuiApplication.clipboard()
    mime = board.mimeData()
    if mime is None or not mime.hasText():
        return "", None
    text = mime.text().strip()
    if len(text) > MAX_URL_LENGTH * 2:
        return "", None
    return text, normalize_url(text)


class ClipboardMonitor(QObject):
    url_detected = Signal(str)

    def __init__(self, clipboard: QClipboard | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clipboard = clipboard or QGuiApplication.clipboard()
        self._enabled = False
        self._last: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._clipboard.dataChanged.connect(self._on_changed)
            log.info("Clipboard URL detection enabled")
        else:
            self._clipboard.dataChanged.disconnect(self._on_changed)
            self._last = None
            log.info("Clipboard URL detection disabled")

    def forget(self) -> None:
        self._last = None

    def _on_changed(self) -> None:
        if not self._enabled or self._clipboard.ownsClipboard():  # our own "Copiar link" is not a new URL
            return
        _text, url = read_clipboard_url(self._clipboard)
        if url is None or url == self._last:
            return
        self._last = url
        log.info("Copied URL detected (not logged)")
        self.url_detected.emit(url)
