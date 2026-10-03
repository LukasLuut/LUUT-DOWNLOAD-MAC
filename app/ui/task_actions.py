"""Executes the actions offered by download cards and history rows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from app.core.models.download import DownloadTask
from app.infrastructure import filesystem as fs
from app.infrastructure.logger import get_logger
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask

log = get_logger("ui.actions")


class TaskActions:
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        self._ctx = context
        self._parent = parent

    def run(self, action: str, task_id: str) -> None:
        manager = self._ctx.downloads
        task = manager.get(task_id) or self._ctx.history.get(task_id)
        if task is None:
            return
        handlers = {
            "pause": lambda: self._pause(task),
            "resume": lambda: manager.resume(task_id),
            "restart": lambda: manager.resume(task_id),
            "retry": lambda: manager.resume(task_id),
            "cancel": lambda: manager.cancel(task_id),
            "remove": lambda: manager.remove(task_id),
            "move_up": lambda: manager.move(task_id, -1),
            "move_down": lambda: manager.move(task_id, 1),
            "move_top": lambda: manager.move_to_top(task_id),
            "open_file": lambda: self.open_file(task),
            "open_folder": lambda: self.open_folder(task),
            "copy_path": lambda: self._copy(task.file_path, "Caminho copiado"),
            "copy_link": lambda: self._copy(task.url, "Link copiado"),
            "redownload": lambda: self.redownload(task),
            "delete": lambda: self.delete_record(task),
            "forget": lambda: self.forget(task),
            "delete_with_file": lambda: self.delete_record_and_file(task),
        }
        handler = handlers.get(action)
        if handler:
            handler()

    def _pause(self, task: DownloadTask) -> None:
        if not self._ctx.downloads.pause(task.id):
            self._ctx.notify("Não foi possível pausar", "Este download não pode ser pausado agora.", "warning")

    def open_file(self, task: DownloadTask) -> None:
        if not task.file_path or not fs.open_file(Path(task.file_path)):
            self._ctx.notify("Arquivo não encontrado", "O arquivo pode ter sido movido ou excluído.", "warning")

    def open_folder(self, task: DownloadTask) -> None:
        target = Path(task.file_path) if task.file_path else Path(task.output_dir)
        try:
            opened = fs.reveal_in_folder(target)
        except OSError as exc:
            log.warning("Could not open folder: %s", exc)
            opened = False
        if not opened:
            self._ctx.notify("Pasta não encontrada", "A pasta deste download não existe mais.", "warning")

    def _copy(self, text: str | None, title: str) -> None:
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self._ctx.notify(title, text, "success")

    def redownload(self, task: DownloadTask) -> None:
        thumbnail = self._ctx.thumbnails.load(task.thumbnail_path)
        self._ctx.downloads.add(task.to_request(), thumbnail)
        self._ctx.notify("Adicionado à fila", task.display_title, "success")

    def delete_record_and_file(self, task: DownloadTask) -> None:
        """Explicit action: the file goes to the Recycle Bin, then the record is removed."""
        path = Path(task.file_path) if task.file_path else None
        if path is None or not path.is_file():
            self._ctx.notify("Arquivo não encontrado", "O arquivo pode ter sido movido ou excluído.", "warning")
            return
        if ask(self._parent, "Excluir registro e arquivo",
               f"O arquivo abaixo será movido para a Lixeira e o registro será removido do histórico.\n\n{path}",
               [("cancel", "Cancelar", None), ("delete", "Excluir registro e arquivo", "danger")]) != "delete":
            return
        if not QFile.moveToTrash(str(path)):
            self._ctx.notify("Não foi possível excluir", "O arquivo está em uso ou sem permissão.", "error")
            return
        self.delete_record(task)
        self._ctx.notify("Excluído", f"{path.name} foi movido para a Lixeira.", "success")

    def forget(self, task: DownloadTask) -> None:
        """"Remover do histórico": leaves the download list and the history (the file itself is kept)."""
        if task.status.is_active:
            return
        self._ctx.downloads.remove(task.id)
        self.delete_record(task)

    def open_download_folder(self) -> None:
        folder = Path(self._ctx.settings.settings.effective_download_dir)
        try:
            opened = fs.reveal_in_folder(folder)
        except OSError as exc:
            log.warning("Could not open folder: %s", exc)
            opened = False
        if not opened:
            self._ctx.notify("Pasta não encontrada", "A pasta de downloads não existe mais.", "warning")

    def delete_record(self, task: DownloadTask) -> None:
        self._ctx.history.delete(task.id)
        if self._ctx.downloads.get(task.id) is None:
            self._ctx.thumbnails.delete(task.thumbnail_path)
