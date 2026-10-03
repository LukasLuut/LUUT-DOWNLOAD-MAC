"""Large URL field with validation feedback."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QWidget

from app.ui import icons, theme
from app.ui.widgets.common import repolish
from app.utils.urls import MAX_URL_LENGTH, normalize_url


class UrlInput(QLineEdit):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("UrlInput")
        self.setPlaceholderText("Cole o link do vídeo…  (Ctrl+V)")
        self.setClearButtonEnabled(True)
        self.setMaxLength(MAX_URL_LENGTH)
        self.setAccessibleName("URL do vídeo")
        self.setToolTip("Cole ou arraste o link de um vídeo. Enter para analisar.")
        self._icon_action = self.addAction(icons.icon("link", theme.palette().text_muted),
                                           QLineEdit.ActionPosition.LeadingPosition)
        self.returnPressed.connect(lambda: self.submitted.emit(self.text()))
        self.textChanged.connect(lambda _: self.set_invalid(False))

    def normalized(self) -> str | None:
        return normalize_url(self.text())

    def set_invalid(self, invalid: bool) -> None:
        if bool(self.property("invalid")) != invalid:
            self.setProperty("invalid", invalid)
            repolish(self)

    def refresh_icon(self) -> None:
        self._icon_action.setIcon(icons.icon("link", theme.palette().text_muted))
