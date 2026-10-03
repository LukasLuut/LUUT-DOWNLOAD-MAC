"""Searchable, filterable download history with export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QButtonGroup, QFileDialog, QLineEdit, QMenu, QWidget

from app.core.services.history import HISTORY_FILTERS, export_csv, export_json
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask
from app.ui.pages.base import Page
from app.ui.task_actions import TaskActions
from app.ui.widgets.common import Card, hbox, make_button, make_label, vbox
from app.ui.widgets.history_item import HistoryItem

PAGE_SIZE = 50


class HistoryPage(Page):
    def __init__(self, context: AppContext, actions: TaskActions) -> None:
        super().__init__("Histórico", "Todos os downloads concluídos, cancelados e com falha.")
        self._ctx = context
        self._actions = actions
        self._filter = "all"
        self._shown = 0
        self._dirty = True

        self.search = QLineEdit()
        self.search.setPlaceholderText("Pesquisar downloads…")
        self.search.setAccessibleName("Pesquisar downloads")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.palette().text_muted), QLineEdit.ActionPosition.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _: self._search_timer.start())

        filters = hbox(spacing=6)
        self._group = QButtonGroup(self)
        for key, (label, _) in HISTORY_FILTERS.items():
            button = make_button(label, "filter")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self._group.addButton(button)
            filters.addWidget(button)

        export = make_button("Exportar", None, "file-export", "Exportar histórico para CSV ou JSON")
        menu = QMenu(export)
        menu.addAction("Exportar CSV…", lambda: self._export("csv"))
        menu.addAction("Exportar JSON…", lambda: self._export("json"))
        export.setMenu(menu)
        clear = make_button("Limpar histórico", "danger", "trash")
        clear.clicked.connect(self.clear_history)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("Transparent")
        self.toolbar.setLayout(hbox(self.search, filters, None, export, clear, spacing=10))
        self.content.addWidget(self.toolbar)
        self.search.setMinimumWidth(260)

        self.count_label = make_label("", "Muted")
        self.content.addWidget(self.count_label)
        self.list_layout = vbox(spacing=10)
        self.content.addLayout(self.list_layout)
        self.more_button = make_button("Carregar mais", None, "arrow-down")
        self.more_button.clicked.connect(self._load_more)
        self.content.addLayout(hbox(None, self.more_button, None))
        self.empty = Card()
        empty_icon = make_label()
        empty_icon.setPixmap(icons.pixmap("history", theme.palette().text_muted, 40))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title = make_label("", "EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text = make_label("", "Muted")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setLayout(vbox(empty_icon, self.empty_title, self.empty_text, spacing=8, margins=(24, 36, 24, 36)))
        self.content.addWidget(self.empty)
        self.finish()

        context.bridge.task_finished.connect(self._mark_dirty)
        context.bridge.task_removed.connect(self._mark_dirty)

    def _mark_dirty(self, *_: object) -> None:
        self._dirty = True
        if self.isVisible():
            self.reload()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self.reload()

    def reload(self) -> None:
        self._dirty = False
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._shown = 0
        self._load_more()

    def _load_more(self) -> None:
        tasks = self._ctx.history.search(self.search.text(), self._filter, PAGE_SIZE + 1, self._shown)
        has_more = len(tasks) > PAGE_SIZE
        for task in tasks[:PAGE_SIZE]:
            item = HistoryItem(task)
            item.action_requested.connect(self._on_action)
            self.list_layout.addWidget(item)
        self._shown += min(len(tasks), PAGE_SIZE)
        self.more_button.setVisible(has_more)
        self.empty.setVisible(self._shown == 0)
        if self._shown == 0:
            filtered = bool(self.search.text().strip()) or self._filter != "all"
            self.empty_title.setText("Nenhum download encontrado." if filtered else "Seu histórico está vazio.")
            self.empty_text.setText("Tente outra busca ou filtro." if filtered
                                    else "Seus downloads concluídos aparecerão aqui.")
        self.count_label.setText(f"{self._shown}{'+' if has_more else ''} registro(s)")

    def _on_action(self, action: str, task_id: str) -> None:
        self._actions.run(action, task_id)
        if action in ("delete", "delete_with_file"):
            self.reload()

    def _export(self, kind: str) -> None:
        default = str(Path.home() / f"historico-luut.{kind}")
        filters = "CSV (*.csv)" if kind == "csv" else "JSON (*.json)"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar histórico", default, filters)
        if not path:
            return
        tasks = self._ctx.history.search(self.search.text(), self._filter, limit=1_000_000)
        try:
            (export_csv if kind == "csv" else export_json)(tasks, Path(path))
        except OSError:
            self._ctx.notify("Falha ao exportar", "O aplicativo não possui permissão para salvar nesta pasta.", "error")
            return
        self._ctx.notify("Histórico exportado", f"{len(tasks)} registro(s) salvos em {path}", "success")

    def clear_history(self) -> None:
        if ask(self, "Limpar histórico", "Todos os registros do histórico serão apagados. Os arquivos baixados "
               "não serão excluídos.\n\nDeseja continuar?",
               [("cancel", "Voltar", None), ("clear", "Limpar histórico", "danger")]) != "clear":
            return
        for task in self._ctx.history.clear():
            if self._ctx.downloads.get(task.id) is None:
                self._ctx.thumbnails.delete(task.thumbnail_path)
        self.reload()
        self._ctx.notify("Histórico limpo", "Os registros foram removidos.", "success")

    def on_shown(self) -> None:
        if self._dirty:
            self.reload()
