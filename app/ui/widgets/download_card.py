"""Card representing one download task, with actions matching its real state."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from app.core.models.download import DownloadStatus, DownloadTask
from app.ui.widgets.animated import ProgressBar
from app.ui.widgets.common import Card, Chip, ElidedLabel, Thumbnail, hbox, icon_button, make_button, make_label, repolish, vbox
from app.utils.formatters import format_bytes, format_eta, format_speed

STATUS_TONES = {
    DownloadStatus.QUEUED: "neutral", DownloadStatus.DOWNLOADING: "accent", DownloadStatus.PROCESSING: "accent",
    DownloadStatus.PAUSED: "warning", DownloadStatus.COMPLETED: "success", DownloadStatus.CANCELLED: "neutral",
    DownloadStatus.FAILED: "error", DownloadStatus.INTERRUPTED: "warning",
}

# (action, label, icon, variant)
_BUTTONS = {
    "pause": ("Pausar", "pause", None),
    "resume": ("Retomar", "play", "primary"),
    "restart": ("Baixar novamente", "refresh", "primary"),
    "retry": ("Tentar novamente", "refresh", "primary"),
    "cancel": ("Cancelar", "x", "danger"),
    "open_file": ("Abrir arquivo", "external", "primary"),
    "open_folder": ("Abrir pasta", "folder-open", None),
    "copy_path": ("Copiar caminho", "copy", None),
}


@lru_cache(maxsize=256)
def thumbnail_bytes(path: str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _primary_actions(task: DownloadTask) -> list[str]:
    status = task.status
    if task.stopping:
        return []
    if status == DownloadStatus.QUEUED:
        return ["pause", "cancel"]
    if status == DownloadStatus.DOWNLOADING:
        return ["pause", "cancel"]
    if status == DownloadStatus.PROCESSING:
        return ["cancel"]
    if status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED):
        return ["resume" if task.has_partial_data else "restart", "cancel"]
    if status == DownloadStatus.FAILED:
        return ["retry"]
    if status == DownloadStatus.CANCELLED:
        return ["restart"]
    return ["open_file", "open_folder", "copy_path"]


def status_message(task: DownloadTask, queue_position: int | None) -> tuple[str, str]:
    """(text, tone) describing the state honestly."""
    status = task.status
    if task.stopping:
        return "Interrompendo com segurança…", "Muted"
    if task.retry_in:
        return f"{task.error_message or 'Falha temporária.'} Nova tentativa em {task.retry_in}s " \
               f"(tentativa {task.attempts + 1} de 3).", "WarningText"
    if task.notice and status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING):
        return task.notice, "WarningText"
    if status == DownloadStatus.QUEUED:
        return (f"Aguardando na fila — posição {queue_position}" if queue_position else "Aguardando na fila"), "Muted"
    if status == DownloadStatus.PROCESSING:
        return "Finalizando o arquivo (combinando áudio e vídeo)…", "Muted"
    if status == DownloadStatus.PAUSED:
        if task.has_partial_data:
            return "Pausado — o progresso parcial foi mantido e o download continuará de onde parou.", "Muted"
        return "Pausado — nenhum dado parcial foi salvo; o download recomeçará do início.", "Muted"
    if status == DownloadStatus.INTERRUPTED:
        if task.has_partial_data:
            return "Download interrompido quando o aplicativo foi fechado. É possível retomar.", "WarningText"
        return "Download interrompido. Não há dados parciais para retomar.", "WarningText"
    if status == DownloadStatus.FAILED:
        return task.error_message or "O download falhou.", "ErrorText"
    if status == DownloadStatus.CANCELLED:
        return "Download cancelado. Arquivos temporários removidos.", "Muted"
    return "", "Muted"


class DownloadCard(Card):
    action_requested = Signal(str, str)  # action, task id
    selected = Signal(str)

    def __init__(self, task: DownloadTask, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self._compact = compact
        self._task = task
        self._thumb_path: str | None = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self.thumbnail = Thumbnail(112 if compact else 144, 63 if compact else 81)
        self.title = ElidedLabel("", "CardTitle")
        self.chip = Chip()
        self.meta = make_label("", "Muted")
        self.progress = ProgressBar()
        self.stats = make_label("", "Secondary")
        self.message = make_label("", "Muted", wrap=True)
        self.done_details = make_label("", "Secondary", wrap=True, selectable=True)

        self._buttons: dict[str, QPushButton] = {}
        for action, (label, icon_name, variant) in _BUTTONS.items():
            button = make_button(label, variant, icon_name)
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a, self.task_id))
            button.hide()
            self._buttons[action] = button
        self.more = icon_button("more", "Mais ações")
        self.more.clicked.connect(self._show_menu)
        actions = hbox(*self._buttons.values(), None, self.more, spacing=8)

        header = hbox(self.title, self.chip, spacing=10)
        body = vbox(header, self.meta, 2, self.progress, self.stats, self.done_details, self.message,
                    actions, spacing=6)
        self.setLayout(hbox(self.thumbnail, body, spacing=16, margins=(16, 14, 16, 14)))
        self.layout().setAlignment(self.thumbnail, Qt.AlignmentFlag.AlignTop)
        self.update_task(task, None)

    # ------------------------------------------------------------------ state
    def update_task(self, task: DownloadTask, queue_position: int | None) -> None:
        self._task = task
        if task.thumbnail_path != self._thumb_path:
            self._thumb_path = task.thumbnail_path
            self.thumbnail.set_image(thumbnail_bytes(task.thumbnail_path) if task.thumbnail_path else None)
        self.title.set_full_text(task.display_title)
        self.chip.setText("✓ Concluído" if task.status == DownloadStatus.COMPLETED else task.status.label)
        self.chip.set_tone(STATUS_TONES[task.status])
        self.meta.setText(f"{task.quality_label}  •  {task.container_label}")
        self._update_progress(task)
        text, tone = status_message(task, queue_position)
        self.message.setText(text)
        self.message.setObjectName(tone)
        repolish(self.message)
        self.message.setVisible(bool(text))
        self._update_done(task)
        visible = set(_primary_actions(task))
        for action, button in self._buttons.items():
            button.setVisible(action in visible)

    def update_progress(self, task: DownloadTask) -> None:
        self._task = task
        self._update_progress(task)

    def _update_progress(self, task: DownloadTask) -> None:
        status = task.status
        progress = task.progress
        has_bytes = progress.downloaded_bytes > 0
        show = status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING) or (
            has_bytes and status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED, DownloadStatus.FAILED,
                                     DownloadStatus.QUEUED))
        self.progress.setVisible(show)
        self.stats.setVisible(show and status != DownloadStatus.QUEUED)
        if not show:
            return
        fraction = progress.fraction
        if status == DownloadStatus.PROCESSING or (status == DownloadStatus.DOWNLOADING and fraction is None):
            self.progress.set_fraction(None)
        else:
            self.progress.set_fraction(fraction or 0.0)
        tone = {DownloadStatus.PAUSED: "warning", DownloadStatus.INTERRUPTED: "warning",
                DownloadStatus.FAILED: "error", DownloadStatus.QUEUED: "muted"}.get(status, "accent")
        self.progress.set_tone(tone)
        self.stats.setText(self._stats_text(task))

    @staticmethod
    def _stats_text(task: DownloadTask) -> str:
        progress = task.progress
        if task.status == DownloadStatus.PROCESSING:
            return "Processando…"
        if not progress.downloaded_bytes and task.status == DownloadStatus.DOWNLOADING:
            return "Conectando…"
        parts: list[str] = []
        if progress.fraction is not None:
            parts.append(f"{progress.fraction * 100:.0f}%")
            parts.append(f"{format_bytes(progress.downloaded_bytes)} / {format_bytes(progress.total_bytes)}")
        elif progress.downloaded_bytes:
            parts.append(f"{format_bytes(progress.downloaded_bytes)} baixados (tamanho total desconhecido)")
        if task.status == DownloadStatus.DOWNLOADING:
            parts.append(format_speed(progress.speed))
            if progress.eta is not None:
                parts.append(f"{format_eta(progress.eta)} restantes")
        return "  •  ".join(parts)

    def _update_done(self, task: DownloadTask) -> None:
        done = task.status == DownloadStatus.COMPLETED and bool(task.file_path)
        self.done_details.setVisible(done and not self._compact)
        if done:
            path = Path(task.file_path or "")
            self.done_details.setText(f"<b>Nome:</b> {path.name}<br><b>Tamanho:</b> {format_bytes(task.file_size)}"
                                      f"<br><b>Local:</b> {path}")

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.selected.emit(self.task_id)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------- menu
    def _show_menu(self) -> None:
        task = self._task
        menu = QMenu(self)
        if task.status == DownloadStatus.QUEUED:
            menu.addAction("Mover para o topo", lambda: self.action_requested.emit("move_top", self.task_id))
            menu.addAction("Mover para cima", lambda: self.action_requested.emit("move_up", self.task_id))
            menu.addAction("Mover para baixo", lambda: self.action_requested.emit("move_down", self.task_id))
            menu.addSeparator()
        menu.addAction("Copiar link", lambda: self.action_requested.emit("copy_link", self.task_id))
        if task.status == DownloadStatus.COMPLETED:
            menu.addAction("Baixar novamente", lambda: self.action_requested.emit("redownload", self.task_id))
        remove = menu.addAction("Remover da lista" if task.status.is_terminal else "Remover da fila",
                                lambda: self.action_requested.emit("remove", self.task_id))
        remove.setEnabled(not task.status.is_active)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))
