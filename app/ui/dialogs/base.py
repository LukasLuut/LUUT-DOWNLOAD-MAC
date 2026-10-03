"""Dialog base: themed background and native dark title bar on Windows."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QWidget

from app.ui import icons, theme
from app.ui.windows import apply_title_bar_theme


class ThemedDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(icons.app_icon())
        self.setModal(True)

    def showEvent(self, event) -> None:  # noqa: N802
        apply_title_bar_theme(self, theme.palette().name == "dark")
        super().showEvent(event)
