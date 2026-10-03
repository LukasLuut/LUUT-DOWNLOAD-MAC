"""Application entry point."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import traceback
from types import TracebackType

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_AUTHOR, APP_NAME, APP_USER_MODEL_ID, APP_VERSION
from app.core.providers.ytdlp_provider import YtDlpProvider
from app.core.services.ytdlp_manager import default_manager
from app.infrastructure.app_data import activate_fresh_profile, wipe_app_data
from app.infrastructure.database import Database
from app.infrastructure.logger import get_logger, setup_logging
from app.system.autostart import AUTOSTART_FLAG
from app.system.single_instance import (
    CMD_SHOW,
    CMD_WIDGET,
    InstanceServer,
    notify_running_instance,
    server_name,
    wait_for_previous_instance,
)
from app.ui import icons, theme
from app.ui.context import AppContext
from app.ui.main_window import MainWindow
from app.ui.restart import FRESH_FLAG, RESET_FLAG
from app.ui.windows import set_app_user_model_id
from app.utils.paths import app_data_dir, database_path, logs_dir

log = get_logger("main")
UNEXPECTED_MESSAGE = "Ocorreu um erro inesperado. Consulte os logs para mais detalhes."


def _ensure_std_streams() -> None:
    """A windowed executable has no console: give libraries somewhere harmless to write."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _extend_macos_path() -> None:
    """Apps opened from Finder get a minimal PATH (/usr/bin:/bin:...). Add the usual Homebrew/Deno folders so yt-dlp
    finds tools the user installed (e.g. Deno for every YouTube format)."""
    if sys.platform != "darwin":
        return
    current = os.environ.get("PATH", "").split(os.pathsep)
    extra = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.deno/bin")]
    os.environ["PATH"] = os.pathsep.join(current + [folder for folder in extra if folder not in current])


def _reopen_on_activate(app: QApplication, window: MainWindow) -> None:
    """macOS: clicking the Dock icon while every window is hidden (tray mode) brings the main window back."""
    if sys.platform != "darwin":
        return

    def on_state(state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive and not any(
                w.isVisible() for w in app.topLevelWidgets() if w.isWindow()):
            window.handle_instance_command(CMD_SHOW)

    app.applicationStateChanged.connect(on_state)


def _install_exception_hook() -> None:
    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        if QApplication.instance() is not None:
            QMessageBox.warning(None, APP_NAME, UNEXPECTED_MESSAGE)

    sys.excepthook = hook


WIDGET_FLAG = "--widget"


def _open_database(attempts: int = 6) -> Database:
    """After a crash the OS may still be releasing the old process's file handles for a moment."""
    for attempt in range(1, attempts + 1):
        try:
            return Database(database_path())
        except sqlite3.OperationalError:
            if attempt == attempts:
                raise
            log.warning("Database busy (attempt %d), retrying", attempt)
            time.sleep(0.5)
    raise RuntimeError("unreachable")


def main() -> int:
    _ensure_std_streams()
    _extend_macos_path()
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "--self-test":
        from app.self_test import run_self_test

        setup_logging(logs_dir())
        return run_self_test(args[1], args[2])
    if len(args) >= 2 and args[0] in ("--ytdlp-status", "--ytdlp-update"):
        from app.self_test import run_ytdlp_command

        setup_logging(logs_dir())
        return run_ytdlp_command(args[0], args[1], args[2] if len(args) > 2 else None)
    fresh = FRESH_FLAG in args
    if fresh:
        activate_fresh_profile()  # isolated, empty profile; the real data is not read or touched

    set_app_user_model_id(APP_USER_MODEL_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)
    app.setStyle("Fusion")
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)

    autostart = AUTOSTART_FLAG in args
    instance_name = server_name("-fresh" if fresh else "")
    if RESET_FLAG in args:
        wait_for_previous_instance(instance_name)
        failed = wipe_app_data(app_data_dir())
        if failed:
            QMessageBox.warning(None, APP_NAME, "Alguns dados não puderam ser apagados porque estavam em uso. "
                                "Feche outras janelas do aplicativo e tente novamente.")
    elif notify_running_instance(instance_name, CMD_WIDGET if WIDGET_FLAG in args else CMD_SHOW):
        return 0  # the running instance keeps the only DownloadManager, queue and database connection

    setup_logging(logs_dir())
    _install_exception_hook()
    log.info("Starting %s %s%s%s", APP_NAME, APP_VERSION, " (fresh start)" if fresh else "",
             " (started with the system)" if autostart else "")
    try:
        db = _open_database()
    except Exception:  # noqa: BLE001
        log.exception("Could not open database")
        QMessageBox.critical(None, APP_NAME, "Não foi possível abrir o banco de dados local. "
                             "Consulte os logs para mais detalhes.")
        return 1

    ytdlp = default_manager()
    ytdlp.recover_interrupted_install()
    context = AppContext.create(db, YtDlpProvider(ytdlp), ytdlp=ytdlp)
    settings = context.settings.settings
    setup_logging(logs_dir(), settings.verbose_logging)
    palette = theme.set_theme(settings.theme)
    app.setStyleSheet(icons.stylesheet(palette))

    window = MainWindow(context)
    context.downloads.restore()

    server = InstanceServer(instance_name)
    server.command_received.connect(window.handle_instance_command)
    _reopen_on_activate(app, window)

    first_run = context.tutorial.should_autostart()
    hidden = (settings.start_minimized or autostart) and not first_run and window.tray.available
    if not hidden:
        window.show()
    QTimer.singleShot(150, lambda: window.start_up(autostart=autostart))
    code = app.exec()
    server.close()
    context.downloads.shutdown()
    db.close()
    log.info("Exited with code %s", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
