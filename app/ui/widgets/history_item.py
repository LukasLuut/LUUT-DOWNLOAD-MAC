"""One row of the history list."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QWidget

from app.core.models.download import DownloadStatus, DownloadTask
from app.ui.widgets.common import Card, Chip, ElidedLabel, Thumbnail, hbox, icon_button, make_label, vbox
from app.ui.widgets.download_card import STATUS_TONES, thumbnail_bytes
from app.utils.formatters import NOT_AVAILABLE, format_bytes, format_datetime


class HistoryItem(Card):
    action_requested = Signal(str, str)

    def __init__(self, task: DownloadTask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = task.id
        thumb = Thumbnail(120, 68)
        if task.thumbnail_path:
            thumb.set_image(thumbnail_bytes(task.thumbnail_path))
        file_name = Path(task.file_path).name if task.file_path else "Arquivo não gerado"
        chip = Chip(task.status.label, STATUS_TONES[task.status])
        meta = "  •  ".join([
            format_datetime(task.finished_at or task.created_at),
            format_bytes(task.file_size) if task.file_size else NOT_AVAILABLE,
            task.quality_label,
            task.container_label,
        ])
        location = ElidedLabel(f"Local: {task.file_path or task.output_dir}", "Muted")
        exists = bool(task.file_path) and Path(task.file_path).exists()
        details = vbox(hbox(ElidedLabel(task.display_title, "CardTitle"), chip, spacing=10),
                       ElidedLabel(file_name, "Secondary"), make_label(meta, "Muted"), location, spacing=3)
        if task.status == DownloadStatus.FAILED and task.error_message:
            details.addWidget(make_label(task.error_message, "ErrorText", wrap=True))

        buttons = []
        for action, icon_name, tip, enabled in (
            ("open_file", "external", "Abrir arquivo", exists),
            ("open_folder", "folder-open", "Abrir pasta", True),
            ("copy_path", "copy", "Copiar caminho", bool(task.file_path)),
            ("redownload", "refresh", "Baixar novamente", True),
        ):
            button = icon_button(icon_name, tip if enabled or action != "open_file" else "Arquivo não encontrado")
            button.setEnabled(enabled)
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a, self.task_id))
            buttons.append(button)
        delete = icon_button("trash", "Excluir")
        menu = QMenu(delete)
        menu.addAction("Excluir registro (mantém o arquivo)", lambda: self.action_requested.emit("delete", self.task_id))
        with_file = menu.addAction("Excluir registro e arquivo…",
                                   lambda: self.action_requested.emit("delete_with_file", self.task_id))
        with_file.setEnabled(exists)
        delete.setMenu(menu)
        buttons.append(delete)
        layout = hbox(thumb, details, hbox(*buttons, spacing=2), spacing=16, margins=(14, 12, 12, 12))
        layout.setStretch(1, 1)
        layout.setAlignment(thumb, Qt.AlignmentFlag.AlignTop)
        self.setLayout(layout)
