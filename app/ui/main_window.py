"""Main window: sidebar navigation, pages, notifications, tray, widget, shortcuts and close behaviour."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QStackedWidget,
    QWidget,
)

from app import APP_NAME, SYSTEM_NAME
from app.core import events as ev
from app.core.models.download import DownloadStatus, DownloadTask
from app.core.models.settings import AppSettings
from app.core.shortcuts import GROUP_APP, GROUP_WIDGET
from app.infrastructure.logger import get_logger, set_verbose
from app.system.global_hotkeys import GlobalHotkeyService, HotkeyBackend
from app.system.single_instance import CMD_WIDGET
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.dialogs.confirm_dialog import ask
from app.ui.dialogs.diagnostics_dialog import DiagnosticsDialog
from app.ui.dialogs.multi_url_dialog import MultiUrlDialog
from app.ui.pages.about_page import AboutPage
from app.ui.pages.base import Page
from app.ui.pages.downloads_page import DownloadsPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.home_page import HomePage
from app.ui.pages.settings_page import SettingsPage
from app.ui.restart import RESET_FLAG, restart_application
from app.ui.shortcuts import ShortcutBinder
from app.ui.task_actions import TaskActions
from app.ui.tray import TrayController
from app.ui.tutorial.controller import TutorialController
from app.ui.widget_controller import WidgetController
from app.ui.widgets.common import refresh_button_icons
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.toast import ToastManager
from app.ui.ytdlp_updates import YtDlpUpdateController
from app.ui.windows import apply_title_bar_theme
from app.utils.paths import is_fresh_profile, logs_dir
from app.utils.urls import normalize_url

log = get_logger("ui")
MIN_WIDTH, MIN_HEIGHT = 1000, 640
_AUTOSTART_KEYS = {"start_with_windows", "widget_start_with_windows"}


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext, hotkey_backend: HotkeyBackend | None = None) -> None:
        super().__init__()
        self._ctx = context
        self._quitting = False
        self._tray_hint_shown = False
        self.setWindowTitle(f"{APP_NAME} — modo limpo (teste)" if is_fresh_profile() else APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.setAcceptDrops(True)
        self._resize_to_screen()

        self._recovery_pending = False
        self.actions = actions = TaskActions(context, self)
        self.sidebar = Sidebar()
        self.stack = QStackedWidget()
        self.home = HomePage(context, actions)
        self.downloads = DownloadsPage(context, actions)
        self.history = HistoryPage(context, actions)
        self.ytdlp_updates = YtDlpUpdateController(context, self)
        self.settings_page = SettingsPage(context, self.ytdlp_updates)
        self.about = AboutPage(context, self.ytdlp_updates)
        self.pages: dict[str, Page] = {"home": self.home, "downloads": self.downloads, "history": self.history,
                                       "settings": self.settings_page, "about": self.about}
        for page in self.pages.values():
            self.stack.addWidget(page)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.toasts = ToastManager(central)

        self.tray = TrayController(context, self)
        self.tray.open_requested.connect(self.show_window)
        self.tray.quit_requested.connect(self._quit_from_tray)
        self.tray.page_requested.connect(self.open_page)
        self.floating = WidgetController(context, actions, self.open_page, self)
        self.floating.visibility_changed.connect(self._update_widget_state)
        self.floating.tour_offer_requested.connect(self.offer_widget_tour)
        self.tray.show_widget_requested.connect(lambda: self.floating.show())
        self.tray.hide_widget_requested.connect(self.floating.hide)

        self.targets = self._tutorial_targets()
        self.tutorial = TutorialController(self, context.tutorial)
        self.tutorial.finished.connect(self._on_tutorial_finished)
        self.shortcuts = ShortcutBinder(context.shortcuts, "app", self, guard=lambda: not self.tutorial.running)
        self._bind_shortcuts()
        self.shortcuts.apply()
        self.hotkeys = GlobalHotkeyService(context.shortcuts, hotkey_backend, self._global_allowed, self)
        self._connect_signals()
        context.set_notifier(self.notify)
        self._update_widget_state()
        self.navigate("home")

    # ------------------------------------------------------------------ setup
    def _resize_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1280, 820)
            return
        available = screen.availableGeometry()
        width = min(1320, int(available.width() * 0.85))
        height = min(860, int(available.height() * 0.88))
        self.resize(max(MIN_WIDTH, width), max(MIN_HEIGHT, height))
        self.move(available.center() - self.rect().center())

    def _connect_signals(self) -> None:
        self.sidebar.page_requested.connect(self.navigate)
        self.home.navigate.connect(self.navigate)
        self.home.open_multi_url.connect(self.open_multi_url)
        self.downloads.navigate.connect(self.navigate)
        self.settings_page.show_diagnostics.connect(lambda: DiagnosticsDialog(self, self._ctx).exec())
        self.settings_page.start_tutorial.connect(self.start_tutorial)
        self.settings_page.start_widget_tour.connect(self.start_widget_tour)
        self.settings_page.reset_requested.connect(self.request_reset)
        self.settings_page.open_about.connect(lambda: self.navigate("about"))
        self.about.start_tutorial.connect(self.start_tutorial)
        bridge = self._ctx.bridge
        bridge.task_finished.connect(self._on_task_finished)
        bridge.event.connect(self._on_event)
        for signal in (bridge.task_added, bridge.task_updated, bridge.task_finished, bridge.task_removed):
            signal.connect(self._update_badges)
        self._ctx.settings.add_listener(self._on_settings)
        updates = self.ytdlp_updates
        updates.install_offered.connect(self._offer_ytdlp_install)
        updates.update_found.connect(self._on_ytdlp_update)
        updates.blocked.connect(self._on_ytdlp_blocked)
        updates.changed.connect(lambda: self.home.show_engine_missing(updates.installed is None
                                                                      and not updates.busy))
        self.home.install_engine.connect(lambda: (self.navigate("settings:updates"), updates.install()))

    def _global_allowed(self, action) -> bool:
        """Widget hotkeys only exist while the widget is enabled."""
        return action.group != GROUP_WIDGET or self._ctx.settings.settings.widget_enabled

    def _bind_shortcuts(self) -> None:
        bind = self.shortcuts.bind
        manager = self.downloads
        # Window
        bind("app.toggle", self.toggle_window)
        bind("app.show", self.show_window)
        bind("app.focus", self.show_window)
        bind("app.hide", self.hide_window)
        bind("app.minimize", self.showMinimized)
        bind("app.maximize", lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
        bind("app.close", self.close)
        # Navigation
        for page in ("home", "downloads", "history", "settings", "about"):
            bind(f"app.open_{page}", lambda p=page: self.open_page(p))
        # URL
        bind("app.new_url", lambda: (self.navigate("home"), self.home.new_url()))
        bind("app.focus_url", self._focus_url)
        bind("app.paste_url", self._paste_shortcut)
        bind("app.clear_url", lambda: (self.navigate("home"), self.home.clear_url()))
        bind("app.analyze", self.home.analyze, self.home.url_input, Qt.ShortcutContext.WidgetShortcut)
        bind("app.start_download", self._add_shortcut)
        # Downloads (act on the card selected in the Downloads page)
        page_context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        bind("downloads.pause_selected", manager.pause_selected, manager, page_context)
        bind("downloads.resume_selected", manager.resume_selected, manager, page_context)
        bind("downloads.cancel_selected", manager.cancel_selected, manager, page_context)
        bind("downloads.retry_selected", lambda: manager.run_on_selected("retry"))
        bind("downloads.open_file", lambda: manager.run_on_selected("open_file"))
        bind("downloads.open_folder", lambda: manager.run_on_selected("open_folder"))
        bind("downloads.copy_path", lambda: manager.run_on_selected("copy_path"))
        bind("downloads.pause_all", self._ctx.downloads.pause_all)
        bind("downloads.resume_all", self._ctx.downloads.resume_all)
        bind("downloads.cancel_all", manager.cancel_all)
        bind("downloads.clear_finished", self._ctx.downloads.clear_finished)
        # System
        bind("system.open_download_folder", self.actions.open_download_folder)
        bind("system.open_logs", lambda: os.startfile(str(logs_dir())))  # type: ignore[attr-defined]
        bind("system.open_tutorial", self.start_tutorial)
        bind("system.show_tray_menu", self.tray.show_menu)
        hint = self.shortcuts.hint
        hint(self.home.analyze_button, "app.analyze", "Analisar o link")
        hint(self.home.add_button, "app.start_download", "Adicionar à fila")
        hint(self.home.paste_button, "app.paste_url", "Colar link da área de transferência")

    # ------------------------------------------------------------- navigation
    def navigate(self, key: str) -> None:
        key, _, tab = key.partition(":")
        page = self.pages.get(key)
        if page is None:
            return
        self.sidebar.set_current(key)
        if tab and page is self.settings_page:
            self.settings_page.select_tab(tab)
        if self.stack.currentWidget() is not page:
            self.stack.setCurrentWidget(page)
            self._fade_in(page)
        page.on_shown()

    def open_page(self, key: str) -> None:
        self.show_window()
        self.navigate(key)

    def _fade_in(self, page: QWidget) -> None:
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", page)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _focus_url(self) -> None:
        self.navigate("home")
        self.home.focus_url()

    def _add_shortcut(self) -> None:
        if self.stack.currentWidget() is self.home:
            self.home.add_to_queue()

    def _paste_shortcut(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)) and not focus.isReadOnly():
            focus.paste()  # a text field keeps its normal Ctrl+V
            return
        self.navigate("home")
        self.home.paste_from_clipboard()

    def open_multi_url(self, text: str = "") -> None:
        MultiUrlDialog(self, self._ctx, text).exec()

    # ----------------------------------------------------------------- tutorial
    @property
    def overlay_host(self) -> QWidget:
        return self.centralWidget()

    def _tutorial_targets(self) -> dict[str, Callable[[], QWidget | None]]:
        return {
            "sidebar": lambda: self.sidebar,
            "nav_downloads": lambda: self.sidebar.button("downloads"),
            "url": lambda: self.home.url_input,
            "analyze": lambda: self.home.analyze_button,
            "info": lambda: self.home.info_view,
            "quality_format": lambda: self.home.format_box,
            "destination": lambda: self.home.destination_box,
            "queue": lambda: self.home.add_button,
            "downloads": lambda: self.downloads.demo_card,
            "history": lambda: self.history.toolbar,
            "settings": lambda: self.settings_page.first_section,
            "settings_widget": lambda: self.settings_page.widget_section,
            "settings_shortcuts": lambda: self.settings_page.shortcuts_panel.sections[GROUP_APP],
        }

    def set_demo(self, enabled: bool) -> None:
        self.home.set_demo(enabled)
        self.downloads.set_demo(enabled)

    def run_tutorial_action(self, action: str) -> None:
        if action == "choose_default_dir":
            current = self._ctx.settings.settings.default_dir
            folder = QFileDialog.getExistingDirectory(self, "Escolher pasta padrão", current)
            if folder:
                folder = str(Path(folder))
                self._ctx.settings.update(default_dir=folder, last_dir="")
                self.home.set_destination(folder)
                self.notify("Pasta padrão definida", folder, "success")

    def start_tutorial(self) -> None:
        if self.tutorial.running:
            return
        self.show_window()
        self.tutorial.start()

    def _on_tutorial_finished(self, completed: bool) -> None:
        self.settings_page.refresh_tutorial_toggle()
        self.settings_page.select_tab("general")
        if completed:
            self.notify("Tutorial concluído", "Cole um link na página inicial para começar.", "success")
        if self._recovery_pending:
            QTimer.singleShot(200, self._offer_recovery)

    def offer_widget_tour(self) -> None:
        choice = ask(self if self.isVisible() else None, "Conheça o Widget do Luut",
                     "O widget permite iniciar downloads sem abrir a janela principal. Quer ver como ele funciona? "
                     "Leva menos de um minuto.", [("later", "Agora não", None), ("start", "Começar", "primary")])
        if choice == "start":
            self.floating.start_tour()
        else:
            self.floating.decline_tour()

    def start_widget_tour(self) -> None:
        if not self._ctx.settings.settings.widget_enabled:
            self.notify("Widget desativado", "Ative o widget em Configurações → Widget para ver o tour.", "warning")
            return
        self.floating.start_tour()

    # ------------------------------------------------------------- startup
    def start_up(self, autostart: bool = False) -> None:
        """First-run tutorial, then (if any) the recovery of the previous session's queue, then the widget."""
        self._recovery_pending = bool(self._ctx.downloads.recoverable())
        settings = self._ctx.settings.settings
        if settings.suggest_clipboard and not autostart:
            self.home.suggest_clipboard_url()
        self._update_badges()
        self._sync_autostart(settings, quiet=True)
        if not autostart or settings.widget_start_with_windows:
            self.floating.start()
        self.ytdlp_updates.start()
        if self._ctx.tutorial.should_autostart() and self.isVisible():
            self.start_tutorial()
        elif self._recovery_pending:
            if not self.isVisible():
                self.show_window()
            self._offer_recovery()

    def handle_instance_command(self, command: str) -> None:
        """A second launch of the app: never a second queue — just bring this instance forward."""
        if command == CMD_WIDGET and self.floating.enabled:
            self.floating.show()
        else:
            self.show_window()

    def _offer_recovery(self) -> None:
        self._recovery_pending = False
        tasks = self._ctx.downloads.recoverable()
        if not tasks:
            self._ctx.downloads.release_queue()
            return
        interrupted = sum(1 for t in tasks if t.status == DownloadStatus.INTERRUPTED)
        detail = f"Encontramos {len(tasks)} download(s) que podem ser recuperados"
        if interrupted:
            detail += f" — {interrupted} estavam em andamento"
        choice = ask(self, "Downloads interrompidos",
                     f"{detail}.\n\nQuando possível, o download continua do ponto em que parou (arquivo .part).",
                     [("discard", "Descartar", "danger"), ("review", "Revisar", None),
                      ("resume", "Retomar todos", "primary")])
        manager = self._ctx.downloads
        if choice == "resume":
            manager.resume_all()
            self.notify("Fila retomada", f"{len(tasks)} download(s) voltaram para a fila.", "success")
        elif choice == "discard":
            if ask(self, "Descartar downloads", "Os downloads pendentes serão cancelados e os arquivos parciais "
                   "removidos. Deseja continuar?",
                   [("back", "Voltar", None), ("discard", "Descartar", "danger")]) == "discard":
                manager.discard_recoverable()
            else:
                self.navigate("downloads")
        else:
            self.navigate("downloads")  # the queue stays held; the banner offers "Retomar fila"

    # ---------------------------------------------------------------- yt-dlp
    def _offer_ytdlp_install(self) -> None:
        """First run (or a missing/broken yt-dlp): downloads need it, so offer to install it right away."""
        if self.tutorial.running:
            self.tutorial.finished.connect(lambda *_: QTimer.singleShot(300, self._offer_ytdlp_install))
            return
        parent = self if self.isVisible() else None
        log.info("yt-dlp not installed: offering installation")
        if ask(parent, "yt-dlp não encontrado", "O Luut Video Downloader precisa do yt-dlp para realizar downloads.\n\n"
               "Ele é baixado do projeto oficial (github.com/yt-dlp/yt-dlp) e verificado antes de ser usado.",
               [("later", "Agora não", None), ("install", "Baixar yt-dlp", "primary")]) == "install":
            self.ytdlp_updates.install()
        else:
            self.home.show_engine_missing(True)

    def _on_ytdlp_update(self, result, manual: bool) -> None:
        updater = self._ctx.update_manager.ytdlp
        settings = self._ctx.settings.settings
        if manual:
            self.open_page("settings:updates")
            return
        if updater.is_dismissed(result.latest):
            return
        if updater.is_busy():  # never interrupt a download: just let the user know
            self.notify("↑ Atualização disponível", f"yt-dlp {result.latest} está disponível. A atualização poderá "
                        "ser feita quando os downloads terminarem.", "info",
                        ("Ver detalhes", lambda: self.open_page("settings:updates")))
            return
        if not settings.ytdlp_ask_before_update:
            self.ytdlp_updates.install(manual=False)
            return
        self.notify("↑ Atualização disponível", "Uma nova versão do yt-dlp está disponível.", "info",
                    ("Atualizar", lambda r=result: self.confirm_ytdlp_update(r)))

    def confirm_ytdlp_update(self, result) -> None:
        choice = ask(self if self.isVisible() else None, "Nova versão do yt-dlp disponível",
                     f"Versão atual: {result.installed}\nNova versão: {result.latest}\n\nDeseja atualizar agora?",
                     [("later", "Depois", None), ("update", "Atualizar", "primary")])
        if choice == "update":
            self.ytdlp_updates.install()
        else:
            self._ctx.update_manager.ytdlp.dismiss(result.latest)

    def _on_ytdlp_blocked(self, manual: bool) -> None:
        if not manual:
            return
        when_idle = self._ctx.settings.settings.ytdlp_update_when_idle
        ask(self if self.isVisible() else None, "Downloads em andamento",
            "Não é possível atualizar o yt-dlp agora.\n\nExistem downloads em andamento.\n\n"
            + ("A atualização será instalada automaticamente quando todos os downloads terminarem."
               if when_idle else "A atualização poderá ser realizada quando todos os downloads terminarem."),
            [("ok", "OK", "primary")])

    # ---------------------------------------------------------------- reset
    def request_reset(self) -> None:
        choice = ask(self, "Restaurar aplicativo",
                     "Isso apagará os dados locais do Luut Video Downloader: histórico, fila, configurações, "
                     "atalhos personalizados, preferências do tutorial, dados temporários e logs.\n\n"
                     "Os vídeos já baixados nas suas pastas NÃO serão apagados.\n\n"
                     "Esta ação não pode ser desfeita.",
                     [("cancel", "Cancelar", None), ("erase", "Apagar dados", "danger")])
        if choice != "erase":
            return
        log.warning("User requested a full reset of local data")
        self._ctx.autostart.apply(False)
        self.quit_app(cancel_downloads=True, restart_args=[RESET_FLAG])

    # ------------------------------------------------------------ notifications
    def notify(self, title: str, message: str, tone: str = "info",
               action: tuple[str, Callable[[], None]] | None = None) -> None:
        if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.toasts.show(title, message, tone, action=action)
        elif not self.tray.notify(title, message, error=tone == "error", on_click=action[1] if action else None):
            self.toasts.show(title, message, tone, action=action)

    @staticmethod
    def _file_name(task: DownloadTask) -> str:
        return Path(task.file_path).name if task.file_path else task.display_title

    def _on_task_finished(self, task: DownloadTask) -> None:
        settings = self._ctx.settings.settings
        if task.status == DownloadStatus.COMPLETED and settings.notify_completed:
            self.notify("Download concluído", self._file_name(task), "success",
                        ("Abrir arquivo", lambda t=task: self.actions.open_file(t)))
        elif task.status == DownloadStatus.FAILED and settings.notify_errors:
            self.notify("Download falhou", f"{task.display_title}: {task.error_message}", "error",
                        ("Tentar novamente", lambda t=task: self._ctx.downloads.resume(t.id)))

    def _on_event(self, name: str, payload: object) -> None:
        if name == ev.DOWNLOAD_STARTED and isinstance(payload, DownloadTask) \
                and self._ctx.settings.settings.notify_started:
            self.notify("Download iniciado", self._file_name(payload), "info")

    def _update_badges(self, *_: object) -> None:
        count = self._ctx.downloads.pending_count()
        self.sidebar.set_active_count(count)
        self.tray.set_active_count(self._ctx.downloads.active_count())

    def _update_widget_state(self) -> None:
        self.tray.set_widget_state(self.floating.enabled, self.floating.is_visible)

    # ---------------------------------------------------------------- settings
    def _on_settings(self, settings: AppSettings, changed: set[str]) -> None:
        if "theme" in changed:
            self.apply_theme(settings.theme)
        if "verbose_logging" in changed:
            set_verbose(settings.verbose_logging)
        if "widget_enabled" in changed:
            self.hotkeys.sync()
            self._update_widget_state()
        if changed & _AUTOSTART_KEYS:
            self._sync_autostart(settings)

    def _sync_autostart(self, settings: AppSettings, quiet: bool = False) -> None:
        wanted = settings.start_with_windows or settings.widget_start_with_windows
        autostart = self._ctx.autostart
        if not autostart.supported:
            if wanted and not quiet:
                self.notify("Indisponível neste modo", "O modo limpo de teste não é registrado para iniciar com o "
                            f"{SYSTEM_NAME}.", "warning")
            return
        if quiet and wanted == autostart.is_enabled() and not wanted:
            return
        if not autostart.apply(wanted) and not quiet:
            self.notify("Não foi possível alterar", f"O {SYSTEM_NAME} não permitiu alterar a inicialização automática.",
                        "error")

    def apply_theme(self, name: str) -> None:
        palette = theme.set_theme(name)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(icons.stylesheet(palette))
        refresh_button_icons(self)
        self.sidebar.refresh_icons()
        self.home.refresh_theme()
        apply_title_bar_theme(self, palette.name == "dark")
        self.update()

    # ------------------------------------------------------------ window/tray
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_window(self) -> None:
        if self.tray.available:
            self._hide_to_tray()
        else:
            self.showMinimized()

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide_window()
        else:
            self.show_window()

    def showEvent(self, event) -> None:  # noqa: N802
        apply_title_bar_theme(self, theme.palette().name == "dark")
        super().showEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and self._ctx.settings.settings.minimize_to_tray and self.tray.available):
            QTimer.singleShot(0, self._hide_to_tray)
        super().changeEvent(event)

    def _hide_to_tray(self) -> None:
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify(APP_NAME, "O aplicativo continua em execução na bandeja do sistema.")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        settings = self._ctx.settings.settings
        if (settings.minimize_to_tray or settings.widget_enabled) and self.tray.available:
            event.ignore()  # the widget (or the tray) keeps working with the same queue
            self._hide_to_tray()
            return
        if self._ctx.downloads.has_running_work():
            choice = ask(self, "Existem downloads em andamento.", "O que deseja fazer?", [
                ("back", "Voltar", None),
                ("cancel", "Cancelar e sair", "danger"),
                ("pause", "Pausar e sair", None),
                ("background", "Continuar na bandeja", "primary"),
            ])
            if choice == "background":
                event.ignore()
                if self.tray.available:
                    self._hide_to_tray()
                else:
                    self.showMinimized()
                return
            if choice not in ("cancel", "pause"):
                event.ignore()
                return
            self.quit_app(cancel_downloads=choice == "cancel", pause_downloads=choice == "pause")
            event.accept()
            return
        self.quit_app()
        event.accept()

    def _quit_from_tray(self) -> None:
        if self._ctx.downloads.has_running_work():
            choice = ask(None if not self.isVisible() else self, "Downloads em andamento", "O que deseja fazer?",
                         [("back", "Voltar", None), ("cancel", "Cancelar e sair", "danger"),
                          ("pause", "Pausar e sair", None), ("background", "Continuar em segundo plano", "primary")])
            if choice not in ("cancel", "pause"):
                return  # "Continuar em segundo plano"/"Voltar": the downloads keep running
            self.quit_app(cancel_downloads=choice == "cancel", pause_downloads=choice == "pause")
            return
        self.quit_app()

    def quit_app(self, cancel_downloads: bool = False, pause_downloads: bool = False,
                 restart_args: list[str] | None = None) -> None:
        self._quitting = True
        log.info("Quitting (cancel: %s, pause: %s, restart: %s)", cancel_downloads, pause_downloads, restart_args)
        self._ctx.downloads.shutdown(cancel=cancel_downloads, pause=pause_downloads)
        self.hotkeys.close()
        self.floating.destroy()
        self.tray.hide()
        if restart_args is not None:
            restart_application(restart_args)
        self.close()
        QApplication.quit()

    # ----------------------------------------------------------- drag & drop
    @staticmethod
    def _dropped_url(event: QDragEnterEvent | QDropEvent) -> str | None:
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                if url.scheme() in ("http", "https"):
                    return normalize_url(url.toString())
        if mime.hasText():
            return normalize_url(mime.text().strip().splitlines()[0] if mime.text().strip() else "")
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._dropped_url(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        url = self._dropped_url(event)
        if url:
            event.acceptProposedAction()
            self.navigate("home")
            self.home.set_url(url, analyze=True)
