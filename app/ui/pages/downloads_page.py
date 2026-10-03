"""Download queue with per-task controls and global actions."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QWidget

from app.core.models.download import DownloadProgress, DownloadStatus, DownloadTask
from app.core.models.settings import CONCURRENCY_OPTIONS
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask
from app.ui.pages.base import Page
from app.ui.task_actions import TaskActions
from app.ui.widgets.common import Card, hbox, make_button, make_label, vbox
from app.ui.widgets.download_card import DownloadCard


DEMO_TASK = DownloadTask(
    url="https://exemplo.invalid/video", output_dir="", title="Exemplo de download (demonstração)",
    quality_height=1080, container="mp4", id="demo", status=DownloadStatus.DOWNLOADING,
    progress=DownloadProgress(342 * 1024 * 1024, 438 * 1024 * 1024, 12.4 * 1024 * 1024, 8),
)


def ordered(tasks: list[DownloadTask]) -> list[DownloadTask]:
    """Active first, then the queue in order, then paused; finished ones last (newest first)."""
    def rank(task: DownloadTask) -> int:
        if task.status.is_active:
            return 0
        return 1 if task.status == DownloadStatus.QUEUED else 2

    pending = sorted((t for t in tasks if not t.status.is_terminal), key=lambda t: (rank(t), t.position))
    finished = sorted((t for t in tasks if t.status.is_terminal),
                      key=lambda t: t.finished_at or t.created_at, reverse=True)
    return pending + finished


class DownloadsPage(Page):
    navigate = Signal(str)

    def __init__(self, context: AppContext, actions: TaskActions) -> None:
        super().__init__("Downloads", "Acompanhe, pause, retome e organize a fila de downloads.")
        self._ctx = context
        self._actions = actions
        self._cards: dict[str, DownloadCard] = {}
        self._positions: dict[str, int] = {}
        self._selected: str | None = None

        self.pause_all = make_button("Pausar todos", None, "pause")
        self.pause_all.clicked.connect(context.downloads.pause_all)
        self.resume_all = make_button("Retomar todos", None, "play")
        self.resume_all.clicked.connect(context.downloads.resume_all)
        self.clear_done = make_button("Limpar concluídos", "ghost", "check", "Remove da lista os downloads finalizados (o histórico é mantido)")
        self.clear_done.clicked.connect(context.downloads.clear_finished)
        self.concurrency = QComboBox()
        self.concurrency.setAccessibleName("Downloads simultâneos")
        self.concurrency.setToolTip("Quantidade máxima de downloads ao mesmo tempo")
        for value in CONCURRENCY_OPTIONS:
            self.concurrency.addItem(str(value), value)
        self.concurrency.setCurrentIndex(CONCURRENCY_OPTIONS.index(context.settings.settings.max_concurrent))
        self.concurrency.currentIndexChanged.connect(
            lambda _: context.settings.update(max_concurrent=int(self.concurrency.currentData())))
        context.settings.add_listener(self._on_settings)

        self.summary = make_label("", "Secondary")
        self.content.addLayout(hbox(self.summary, None, make_label("Simultâneos", "Muted"), self.concurrency,
                                    12, self.pause_all, self.resume_all, self.clear_done, spacing=8))

        self.held_banner = self._build_held_banner()
        self.content.addWidget(self.held_banner)
        self.demo_holder = vbox(spacing=0)
        self.content.addLayout(self.demo_holder)
        self._demo_card: DownloadCard | None = None
        self.list_layout = vbox(spacing=12)
        self.content.addLayout(self.list_layout)
        self.empty = self._build_empty()
        self.content.addWidget(self.empty)
        self.finish()

        self._reorder_timer = QTimer(self)
        self._reorder_timer.setSingleShot(True)
        self._reorder_timer.setInterval(50)
        self._reorder_timer.timeout.connect(self._reorder)

        bridge = context.bridge
        bridge.task_added.connect(self._upsert)
        bridge.task_updated.connect(self._upsert)
        bridge.task_finished.connect(self._upsert)
        bridge.task_progress.connect(self._on_progress)
        bridge.task_removed.connect(self._remove)
        for task in context.downloads.tasks():
            self._upsert(task)

    def _build_held_banner(self) -> QWidget:
        banner = Card()
        banner.setObjectName("Banner")
        icon = make_label()
        icon.setPixmap(icons.pixmap("life-buoy", theme.palette().accent_hover, 20))
        text = make_label("A fila foi recuperada e está pausada. Revise os itens e retome quando quiser.", "Secondary",
                          wrap=True)
        resume = make_button("Retomar fila", "primary", "play")
        resume.clicked.connect(self._ctx.downloads.resume_all)
        banner.setLayout(hbox(icon, text, resume, spacing=12, margins=(14, 10, 10, 10)))
        banner.layout().setStretch(1, 1)
        banner.hide()
        return banner

    def _build_empty(self) -> QWidget:
        card = Card()
        icon = make_label()
        icon.setPixmap(icons.pixmap("inbox", theme.palette().text_muted, 40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        go = make_button("Novo download", "primary", "link")
        go.clicked.connect(lambda: self.navigate.emit("home"))
        title = make_label("Nenhum download em andamento", "EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = make_label("Cole uma URL para começar.", "Muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.setLayout(vbox(icon, title, text, hbox(None, go, None), spacing=10, margins=(24, 36, 24, 36)))
        return card

    # ------------------------------------------------------------------ events
    def _on_settings(self, settings, changed: set[str]) -> None:
        if "max_concurrent" in changed:
            self.concurrency.blockSignals(True)
            self.concurrency.setCurrentIndex(CONCURRENCY_OPTIONS.index(settings.max_concurrent))
            self.concurrency.blockSignals(False)
            self._ctx.downloads.reschedule()

    def _upsert(self, task: DownloadTask) -> None:
        card = self._cards.get(task.id)
        if card is None:
            card = DownloadCard(task)
            card.action_requested.connect(self._actions.run)
            card.selected.connect(self.select)
            self._cards[task.id] = card
        card.update_task(task, self._positions.get(task.id))
        self._reorder_timer.start()

    def _on_progress(self, task: DownloadTask) -> None:
        card = self._cards.get(task.id)
        if card:
            card.update_progress(task)

    def _remove(self, task: DownloadTask) -> None:
        card = self._cards.pop(task.id, None)
        if card:
            card.deleteLater()
        if self._selected == task.id:
            self._selected = None
        self._reorder_timer.start()

    def _reorder(self) -> None:
        manager = self._ctx.downloads
        tasks = ordered(manager.tasks())
        positions = self._positions = manager.queue_positions()
        while self.list_layout.count():
            self.list_layout.takeAt(0)
        for task in tasks:
            card = self._cards.get(task.id)
            if card is None:
                continue
            card.update_task(task, positions.get(task.id))
            self.list_layout.addWidget(card)
            card.show()
        self.empty.setVisible(not tasks and self._demo_card is None)
        self.held_banner.setVisible(manager.is_held and any(not t.status.is_terminal for t in tasks))
        active = sum(1 for t in tasks if t.status.is_active)
        waiting = sum(1 for t in tasks if t.status == DownloadStatus.QUEUED)
        done = sum(1 for t in tasks if t.status.is_terminal)
        self.summary.setText(f"{active} {'download ativo' if active == 1 else 'downloads ativos'}  •  "
                             f"{waiting} na fila  •  {done} finalizados")
        resumable = any(t.status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED) for t in tasks)
        self.pause_all.setEnabled(active + waiting > 0)
        self.resume_all.setEnabled(resumable)
        self.clear_done.setEnabled(done > 0)

    def select(self, task_id: str) -> None:
        if self._selected and self._selected in self._cards:
            self._cards[self._selected].set_selected(False)
        self._selected = task_id
        if task_id in self._cards:
            self._cards[task_id].set_selected(True)

    def remove_selected(self) -> bool:
        """Delete key: remove the selected item from the queue (not while it is downloading)."""
        if not self._selected:
            return False
        task = self._ctx.downloads.get(self._selected)
        if task is None:
            return False
        if task.status.is_active:
            self._ctx.notify("Download em andamento", "Cancele ou pause o download antes de removê-lo.", "warning")
            return True
        self._ctx.downloads.remove(task.id)
        return True

    # ------------------------------------------------- keyboard commands (selected card)
    def selected_task(self) -> DownloadTask | None:
        return self._ctx.downloads.get(self._selected) if self._selected else None

    def _click_focused_button(self) -> bool:
        focus = QApplication.focusWidget()
        if isinstance(focus, QAbstractButton) and self.isAncestorOf(focus):
            focus.click()  # Space keeps pressing the focused button when nothing is selected
            return True
        return False

    def pause_selected(self) -> bool:
        task = self.selected_task()
        if task is None:
            return self._click_focused_button()
        if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.QUEUED):
            self._actions.run("pause", task.id)
            return True
        return False  # not pausable: "Retomar selecionado" (same key) gets a chance

    def resume_selected(self) -> bool:
        task = self.selected_task()
        if task is None:
            return self._click_focused_button()
        if task.status in (DownloadStatus.PAUSED, DownloadStatus.INTERRUPTED):
            self._actions.run("resume", task.id)
            return True
        return False

    def cancel_selected(self) -> bool:
        task = self.selected_task()
        if task is None:
            return False
        if task.status.is_terminal:
            return self.remove_selected()
        if task.status.is_active and ask(
                self, "Cancelar download", f"Cancelar “{task.display_title}”? O arquivo parcial será removido.",
                [("back", "Voltar", None), ("cancel", "Cancelar download", "danger")]) != "cancel":
            return True
        self._ctx.downloads.cancel(task.id)
        return True

    def run_on_selected(self, action: str) -> bool:
        task = self.selected_task()
        if task is None:
            return False
        if action == "retry":
            if task.status.is_active or task.status == DownloadStatus.QUEUED:
                return False
            action = "redownload" if task.status == DownloadStatus.COMPLETED else "retry"
        self._actions.run(action, task.id)
        return True

    def cancel_all(self) -> None:
        if not any(t.is_pending for t in self._ctx.downloads.tasks()):
            return
        if ask(self, "Cancelar todos", "Todos os downloads pendentes serão cancelados e os arquivos parciais "
               "removidos.", [("back", "Voltar", None), ("cancel", "Cancelar todos", "danger")]) == "cancel":
            self._ctx.downloads.cancel_all()

    def set_demo(self, enabled: bool) -> None:
        """Tutorial sample card: labelled "Exemplo", never added to the manager or the database."""
        if enabled and self._demo_card is None:
            card = DownloadCard(DEMO_TASK)
            card.chip.setText("Exemplo")
            card.chip.set_tone("warning")
            card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.demo_holder.addWidget(card)
            self._demo_card = card
        elif not enabled and self._demo_card is not None:
            self._demo_card.deleteLater()
            self._demo_card = None
        self._reorder()

    @property
    def demo_card(self) -> DownloadCard | None:
        return self._demo_card

    def on_shown(self) -> None:
        self._reorder()
