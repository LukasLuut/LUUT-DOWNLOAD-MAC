"""Styled confirmation dialog with any number of buttons."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from app.ui.dialogs.base import ThemedDialog
from app.ui.widgets.common import hbox, make_button, make_label, vbox

ButtonSpec = tuple[str, str, str | None]  # key, label, variant


class ConfirmDialog(ThemedDialog):
    def __init__(self, parent: QWidget | None, title: str, message: str, buttons: list[ButtonSpec]) -> None:
        super().__init__(parent, title)
        self.choice: str | None = None
        self.setMinimumWidth(460)
        row = hbox(None, spacing=10)
        for index, (key, label, variant) in enumerate(buttons):
            button = make_button(label, variant)
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            row.addWidget(button)
            if index == len(buttons) - 1:
                button.setDefault(True)
                button.setFocus()
        self.setLayout(vbox(make_label(title, "SectionTitle"), make_label(message, "Secondary", wrap=True),
                            12, row, spacing=10, margins=(24, 22, 24, 20)))

    def _choose(self, key: str) -> None:
        self.choice = key
        self.accept()


def ask(parent: QWidget | None, title: str, message: str, buttons: list[ButtonSpec]) -> str | None:
    """Show the dialog and return the chosen key (None if closed)."""
    dialog = ConfirmDialog(parent, title, message, buttons)
    dialog.exec()
    return dialog.choice
