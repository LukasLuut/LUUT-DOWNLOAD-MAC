"""Dashboard: URL analysis, download options, summary and recent downloads."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QFileDialog, QGridLayout, QLineEdit, QWidget

from app.core.errors import FRIENDLY_MESSAGES, ErrorKind, to_app_error
from app.core.models.download import DownloadStatus, DownloadTask, request_from_info
from app.core.models.video import (
    CONTAINER_AUDIO, CONTAINER_MP4, CONTAINER_ORIGINAL, KIND_AUDIO, QualityOption, VideoInfo, container_kind,
    container_label,
)
from app.core.services.analyzer import AnalysisResult
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.pages.base import Page, ResponsiveRow
from app.ui.task_actions import TaskActions
from app.ui.widgets.animated import Spinner
from app.ui.widgets.common import (
    Card, Chip, ElidedLabel, Separator, hbox, icon_button, make_button, make_label, vbox,
)
from app.ui.widgets.download_card import STATUS_TONES, DownloadCard
from app.ui.widgets.url_input import UrlInput
from app.ui.widgets.video_info_card import VideoInfoView
from app.ui.workers import run_in_background
from app.utils.formatters import format_bytes, format_datetime
from app.utils.urls import normalize_url

_EMPTY, _LOADING, _ERROR, _RESULT = range(4)

DEMO_INFO = VideoInfo(
    url="https://exemplo.invalid/video", title="Vídeo de exemplo (demonstração)", duration=522,
    uploader="Canal de exemplo", extractor="Exemplo", max_height=1080, source_formats=["mp4", "webm"],
    qualities=[QualityOption(0, 438 * 1024 * 1024), QualityOption(1080, 438 * 1024 * 1024),
               QualityOption(720, 190 * 1024 * 1024), QualityOption(480, 96 * 1024 * 1024)],
    containers=[CONTAINER_MP4, CONTAINER_ORIGINAL, CONTAINER_AUDIO], audio_size=12 * 1024 * 1024,
    audio_format="MP3",
)


class StatCard(Card):
    def __init__(self, label: str, icon_name: str, tone: str) -> None:
        super().__init__()
        palette = theme.palette()
        color = {"success": palette.success, "error": palette.error, "accent": palette.accent}.get(tone, palette.text_secondary)
        badge = make_label()
        badge.setPixmap(icons.pixmap(icon_name, color, 20))
        self.value = make_label("0", "StatValue")
        self.setLayout(vbox(hbox(make_label(label, "StatLabel"), None, badge), self.value,
                            spacing=4, margins=(18, 14, 18, 14)))
        self.setMinimumWidth(150)


class RecentRow(QWidget):
    def __init__(self, task: DownloadTask, on_action) -> None:
        super().__init__()
        self.setObjectName("Transparent")
        title = ElidedLabel(task.display_title, "Secondary")
        chip = Chip(task.status.label, STATUS_TONES[task.status])
        date = make_label(format_datetime(task.finished_at or task.created_at), "Muted")
        button = icon_button("folder-open", "Abrir pasta")
        button.clicked.connect(lambda: on_action("open_folder", task.id))
        self.setLayout(hbox(title, chip, date, button, spacing=10))
        self.layout().setStretch(0, 1)


class HomePage(Page):
    open_multi_url = Signal(str)  # prefilled text
    navigate = Signal(str)
    install_engine = Signal()

    def __init__(self, context: AppContext, actions: TaskActions) -> None:
        super().__init__("Início", "Cole o link de um vídeo para analisar, escolher a qualidade e baixar.")
        self._ctx = context
        self._actions = actions
        self._request_id = 0
        self._result: AnalysisResult | None = None
        self._demo_saved: tuple[AnalysisResult | None, int] | None = None
        self._state = _EMPTY

        self._build_engine_banner()
        self._build_clipboard_banner()
        self._build_download_card()
        self._build_summary()
        self.finish()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self.refresh_dashboard)
        bridge = context.bridge
        for signal in (bridge.task_added, bridge.task_updated, bridge.task_finished, bridge.task_removed):
            signal.connect(lambda *_: self._refresh_timer.start())
        bridge.task_progress.connect(self._on_progress)
        self.refresh_dashboard()

    # ---------------------------------------------------------------- building
    def _build_engine_banner(self) -> None:
        self.engine_banner = Card()
        self.engine_banner.setObjectName("Banner")
        icon = make_label()
        icon.setPixmap(icons.pixmap("alert-triangle", theme.palette().warning, 20))
        text = make_label("<b>yt-dlp não está instalado.</b> O Luut Video Downloader precisa dele para analisar e "
                          "baixar vídeos.", "Secondary", wrap=True)
        install = make_button("Instalar yt-dlp", "primary", "download")
        install.clicked.connect(self.install_engine.emit)
        self.engine_banner.setLayout(hbox(icon, text, install, spacing=12, margins=(14, 10, 10, 10)))
        self.engine_banner.layout().setStretch(1, 1)
        self.engine_banner.hide()
        self.content.addWidget(self.engine_banner)

    def show_engine_missing(self, missing: bool) -> None:
        self.engine_banner.setVisible(missing)

    def _build_clipboard_banner(self) -> None:
        self.banner = Card()
        self.banner.setObjectName("Banner")
        self._banner_url = ""
        self.banner_text = ElidedLabel("", "Secondary")
        use = make_button("Usar link", "primary")
        use.clicked.connect(self._use_clipboard_url)
        dismiss = icon_button("x", "Dispensar")
        dismiss.clicked.connect(self.banner.hide)
        icon = make_label()
        icon.setPixmap(icons.pixmap("clipboard", theme.palette().accent_hover, 18))
        self.banner.setLayout(hbox(icon, self.banner_text, use, dismiss, spacing=12, margins=(14, 8, 8, 8)))
        self.banner.layout().setStretch(1, 1)
        self.banner.hide()
        self.content.addWidget(self.banner)

    def _build_download_card(self) -> None:
        card = Card(shadow=True)
        self.url_input = UrlInput()  # Enter = "app.analyze", bound by the main window through the ShortcutManager
        paste = self.paste_button = make_button("Colar", None, "clipboard", "Colar link da área de transferência")
        paste.clicked.connect(self.paste_from_clipboard)
        self.analyze_button = make_button("Analisar vídeo", "primary", "search", "Analisar o link")
        self.analyze_button.clicked.connect(self.analyze)
        multi = make_button("Adicionar vários links", "link", "list-plus")
        multi.clicked.connect(lambda: self.open_multi_url.emit(""))
        import_list = make_button("Importar lista (.txt)", "link", "file-import")
        import_list.clicked.connect(self.import_list)

        self._states = [QWidget(), self._build_loading(), self._build_error(), self._build_result()]
        self._set_state(_EMPTY)

        card.setLayout(vbox(
            make_label("URL DO VÍDEO", "FieldLabel"),
            hbox(self.url_input, paste, self.analyze_button, spacing=10),
            hbox(multi, import_list, None, spacing=16),
            *self._states,
            spacing=10, margins=(24, 20, 24, 22)))
        self.content.addWidget(card)

    def _set_state(self, index: int) -> None:
        self._state = index
        for i, widget in enumerate(self._states):
            widget.setVisible(i == index and i != _EMPTY)

    def _build_loading(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("Transparent")
        widget.setLayout(hbox(Spinner(24), make_label("Analisando vídeo…", "Secondary"), None,
                              spacing=12, margins=(0, 14, 0, 6)))
        return widget

    def _build_error(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("Transparent")
        icon = make_label()
        icon.setPixmap(icons.pixmap("alert-circle", theme.palette().error, 22))
        self.error_label = make_label("", "ErrorText", wrap=True)
        retry = make_button("Tentar novamente", None, "refresh")
        retry.clicked.connect(self.analyze)
        widget.setLayout(hbox(icon, self.error_label, retry, spacing=12, margins=(0, 14, 0, 6)))
        widget.layout().setStretch(1, 1)
        return widget

    def _build_result(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("Transparent")
        self.info_view = VideoInfoView()
        self.quality = QComboBox()
        self.quality.setAccessibleName("Qualidade")
        self.quality.setToolTip("Somente qualidades realmente disponíveis são listadas")
        self.container = QComboBox()
        self.container.setAccessibleName("Formato")
        self.container.currentIndexChanged.connect(self._on_container_changed)
        self.destination = QLineEdit()
        self.destination.setAccessibleName("Pasta de destino")
        self.destination.setToolTip("Pasta onde o arquivo será salvo")
        browse = icon_button("folder", "Escolher pasta de destino")
        browse.clicked.connect(self._browse_destination)
        self.add_button = make_button("Adicionar à fila", "primary", "download", "Adicionar à fila")
        self.add_button.setMinimumHeight(40)
        self.add_button.clicked.connect(self.add_to_queue)

        self.format_box = QWidget()
        self.format_box.setObjectName("Transparent")
        grid = QGridLayout(self.format_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(make_label("QUALIDADE", "FieldLabel"), 0, 0)
        grid.addWidget(make_label("FORMATO", "FieldLabel"), 0, 1)
        grid.addWidget(self.quality, 1, 0)
        grid.addWidget(self.container, 1, 1)
        self.destination_box = QWidget()
        self.destination_box.setObjectName("Transparent")
        self.destination_box.setLayout(vbox(make_label("DESTINO", "FieldLabel"),
                                            hbox(self.destination, browse, spacing=6), spacing=6))
        options = hbox(self.format_box, self.destination_box, spacing=14)
        options.setStretch(0, 4)
        options.setStretch(1, 4)
        self.demo_chip = Chip("Demonstração — exemplo visual, nenhum download real", "warning")
        self.demo_chip.hide()
        widget.setLayout(vbox(8, self.demo_chip, self.info_view, 6, Separator(), 6, options,
                              hbox(None, self.add_button), spacing=10))
        return widget

    def _build_summary(self) -> None:
        stats = ResponsiveRow(760, spacing=14)
        self.stat_total = StatCard("Downloads hoje", "download", "accent")
        self.stat_done = StatCard("Concluídos", "check-circle", "success")
        self.stat_active = StatCard("Em andamento", "activity", "accent")
        self.stat_failed = StatCard("Falharam", "alert-circle", "error")
        for card in (self.stat_total, self.stat_done, self.stat_active, self.stat_failed):
            stats.add(card)
        self.content.addWidget(stats)

        row = ResponsiveRow(980)
        current = Card()
        self.current_holder = vbox(spacing=10)
        self.current_empty = make_label("Nenhum download em andamento.", "Muted")
        self.current_holder.addWidget(self.current_empty)
        self._current_card: DownloadCard | None = None
        see_all = make_button("Ver fila", "link")
        see_all.clicked.connect(lambda: self.navigate.emit("downloads"))
        current.setLayout(vbox(hbox(make_label("Download atual", "SectionTitle"), None, see_all),
                               self.current_holder, None, spacing=12, margins=(20, 16, 20, 18)))
        recent = Card()
        self.recent_holder = vbox(spacing=6)
        see_history = make_button("Ver histórico", "link")
        see_history.clicked.connect(lambda: self.navigate.emit("history"))
        recent.setLayout(vbox(hbox(make_label("Últimos downloads", "SectionTitle"), None, see_history),
                              self.recent_holder, None, spacing=12, margins=(20, 16, 20, 18)))
        row.add(current, 3)
        row.add(recent, 2)
        self.activity_row = row
        self.content.addWidget(row)

        self.hello_card = Card()
        hello_icon = make_label()
        hello_icon.setPixmap(icons.pixmap("hand", theme.palette().accent_hover, 40))
        hello_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hello_title = make_label("Olá!", "EmptyTitle")
        hello_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hello_text = make_label("Você ainda não possui downloads.\nCole um link acima para começar.", "Secondary")
        hello_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hello_card.setLayout(vbox(hello_icon, hello_title, hello_text, spacing=8, margins=(24, 28, 24, 28)))
        self.content.addWidget(self.hello_card)

    # ---------------------------------------------------------------- analysis
    def new_url(self) -> None:
        self._result = None
        self.url_input.clear()
        self._set_state(_EMPTY)
        self.focus_url()

    def clear_url(self) -> None:
        self.url_input.clear()
        self.url_input.setFocus()

    def focus_url(self) -> None:
        self.url_input.setFocus()
        self.url_input.selectAll()

    def set_url(self, text: str, analyze: bool = False) -> None:
        self.url_input.setText(text.strip())
        if analyze:
            self.analyze()

    def paste_from_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.set_url(text, analyze=normalize_url(text) is not None)
        self.url_input.setFocus()

    def analyze(self) -> None:
        url = normalize_url(self.url_input.text())
        if url is None:
            self.url_input.set_invalid(True)
            empty = not self.url_input.text().strip()
            self._show_error("Cole um link para analisar." if empty else FRIENDLY_MESSAGES[ErrorKind.INVALID_URL])
            return
        self._request_id += 1
        request_id = self._request_id
        self.analyze_button.setEnabled(False)
        self._set_state(_LOADING)
        run_in_background(lambda: self._ctx.analyzer.analyze(url),
                          lambda result: self._analysis_done(request_id, result),
                          lambda exc: self._analysis_failed(request_id, exc))

    def _analysis_done(self, request_id: int, result: AnalysisResult) -> None:
        if request_id != self._request_id:
            return
        self.analyze_button.setEnabled(True)
        self._result = result
        self._populate(result)
        self._set_state(_RESULT)
        self.add_button.setFocus()

    def _populate(self, result: AnalysisResult) -> None:
        info = result.info
        self.info_view.show_info(info, result.thumbnail)
        self.quality.clear()
        for option in info.qualities:
            size = f"   ~{format_bytes(option.estimated_size)}" if option.estimated_size else ""
            self.quality.addItem(f"{option.label}{size}", option.height)
        self.container.clear()
        for key in info.all_containers():
            self.container.addItem(container_label(key, info), key)
        self.destination.setText(self._ctx.settings.settings.effective_download_dir)

    def _on_container_changed(self, _index: int = 0) -> None:
        audio = container_kind(str(self.container.currentData() or "")) == KIND_AUDIO
        self.quality.setEnabled(not audio)
        self.quality.setToolTip("Somente áudio usa sempre a melhor qualidade de áudio disponível" if audio
                                else "Somente qualidades realmente disponíveis são listadas")
        if audio:
            self.quality.setCurrentIndex(0)

    def _analysis_failed(self, request_id: int, exc: BaseException) -> None:
        if request_id != self._request_id:
            return
        self.analyze_button.setEnabled(True)
        self._show_error(to_app_error(exc).user_message)

    def _show_error(self, message: str) -> None:
        self._result = None
        self.error_label.setText(message)
        self._set_state(_ERROR)

    def _browse_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", self.destination.text())
        if folder:
            self.destination.setText(str(Path(folder)))

    def add_to_queue(self) -> None:
        if self._demo_saved is not None:
            return  # tutorial demo: never creates a real download
        if self._result is None:
            if self.url_input.text().strip():
                self.analyze()
            return
        destination = self.destination.text().strip()
        if not destination or not Path(destination).is_absolute():
            self._ctx.notify("Destino inválido", "Escolha uma pasta de destino válida.", "error")
            return
        info = self._result.info
        request = request_from_info(info, destination, str(self.container.currentData()),
                                    int(self.quality.currentData() or 0))
        self._ctx.downloads.add(request, self._result.thumbnail)
        self._ctx.settings.update(last_dir=destination)
        self._ctx.notify("Adicionado à fila", info.title or info.url, "success")
        self._result = None
        self.url_input.clear()
        self._set_state(_EMPTY)
        self.url_input.setFocus()

    def set_demo(self, enabled: bool) -> None:
        """Tutorial sample: purely visual, clearly labelled, never stored or counted."""
        if enabled == (self._demo_saved is not None):
            return
        if enabled:
            self._demo_saved = (self._result, self._state)
            self._populate(AnalysisResult(DEMO_INFO, None))
            self.demo_chip.show()
            self._set_state(_RESULT)
            return
        saved_result, saved_state = self._demo_saved
        self._demo_saved = None
        self.demo_chip.hide()
        self._result = saved_result
        if saved_result is not None:
            self._populate(saved_result)
        self._set_state(saved_state if saved_result is not None or saved_state != _RESULT else _EMPTY)

    def has_result(self) -> bool:
        return self._result is not None

    def import_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar lista de links", "", "Arquivos de texto (*.txt)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as handle:
                text = handle.read(1_000_000)
        except OSError:
            self._ctx.notify("Não foi possível ler o arquivo", "Verifique se o arquivo existe e pode ser aberto.", "error")
            return
        self.open_multi_url.emit(text)

    # --------------------------------------------------------------- clipboard
    def suggest_clipboard_url(self) -> None:
        url = normalize_url(QGuiApplication.clipboard().text())
        if url and not self.url_input.text():
            self._banner_url = url
            self.banner_text.set_full_text(f"Usar URL copiada?  {url}")
            self.banner.show()

    def _use_clipboard_url(self) -> None:
        self.banner.hide()
        self.set_url(self._banner_url, analyze=True)

    # --------------------------------------------------------------- dashboard
    def set_destination(self, folder: str) -> None:
        self.destination.setText(folder)

    def refresh_dashboard(self) -> None:
        stats = self._ctx.history.daily_stats()
        self.stat_total.value.setText(str(stats.total))
        self.stat_done.value.setText(str(stats.completed))
        self.stat_active.value.setText(str(self._ctx.downloads.active_count()))
        self.stat_failed.value.setText(str(stats.failed))
        self._refresh_current()
        self._refresh_recent()
        empty = not self._ctx.downloads.tasks() and self._ctx.history.count() == 0
        self.hello_card.setVisible(empty)
        self.activity_row.setVisible(not empty)

    def _current_task(self) -> DownloadTask | None:
        tasks = self._ctx.downloads.tasks()
        active = [t for t in tasks if t.status.is_active]
        return active[0] if active else next((t for t in tasks if t.status == DownloadStatus.QUEUED), None)

    def _refresh_current(self) -> None:
        task = self._current_task()
        if task is None:
            if self._current_card:
                self._current_card.deleteLater()
                self._current_card = None
            self.current_empty.show()
            return
        self.current_empty.hide()
        if self._current_card is None or self._current_card.task_id != task.id:
            if self._current_card:
                self._current_card.deleteLater()
            self._current_card = DownloadCard(task, compact=True)
            self._current_card.setObjectName("CardAlt")
            self._current_card.action_requested.connect(self._actions.run)
            self.current_holder.addWidget(self._current_card)
        self._current_card.update_task(task, self._ctx.downloads.queue_position(task.id))

    def _on_progress(self, task: DownloadTask) -> None:
        if self._current_card and self._current_card.task_id == task.id:
            self._current_card.update_progress(task)

    def _refresh_recent(self) -> None:
        while self.recent_holder.count():
            item = self.recent_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        recent = self._ctx.history.recent(5)
        if not recent:
            self.recent_holder.addWidget(make_label("Seus downloads concluídos aparecerão aqui.", "Muted"))
        for task in recent:
            self.recent_holder.addWidget(RecentRow(task, self._actions.run))

    def on_shown(self) -> None:
        self.refresh_dashboard()

    def refresh_theme(self) -> None:
        self.url_input.refresh_icon()
