"""Qt side of the yt-dlp updates: runs checks/installs off the UI thread and publishes a simple state for the views.

The interface (Configurações → Atualizações, Sobre, Diagnóstico, first-run prompt) only reads `state`/`message` and
calls `check()` / `install()` / `cancel()`; all decisions live in `YtDlpUpdater`, all mechanics in `YtDlpManager`.
"""

from __future__ import annotations

import threading
from enum import Enum

from PySide6.QtCore import QObject, Signal
from shiboken6 import isValid

from app.core import events as ev
from app.core.services.updates import UpdateBlocked
from app.core.services.ytdlp_manager import UpdateCancelled, UpdateCheck, UpdateStatus, YtDlpError
from app.infrastructure.logger import get_logger
from app.ui.context import AppContext
from app.ui.workers import run_in_background

log = get_logger("ui.updates")


class Phase(str, Enum):
    IDLE = "idle"
    READING = "reading"          # running `yt-dlp --version`
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"


class YtDlpUpdateController(QObject):
    changed = Signal()
    progress = Signal(int, object)                 # bytes done, total (None = unknown)
    install_offered = Signal()                    # yt-dlp missing at startup
    update_found = Signal(object, bool)           # UpdateCheck, manual
    blocked = Signal(bool)                        # manual?
    finished = Signal(bool, str, bool)            # success, message, first install
    _progress_from_thread = Signal(str, int, object)

    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = context
        self.updater = context.update_manager.ytdlp
        self.phase = Phase.IDLE
        self.installed: str | None = None
        self.path = None
        self.last_result: UpdateCheck | None = None
        self.error: str | None = None
        self._cancel: threading.Event | None = None
        self._progress_from_thread.connect(self._on_thread_progress)
        context.bridge.event.connect(self._on_event)

    # --------------------------------------------------------------- state
    @property
    def status(self) -> UpdateStatus | None:
        if self.installed is None and self.phase not in (Phase.READING,):
            return UpdateStatus.NOT_INSTALLED
        if self.last_result is not None:
            if self.last_result.status == UpdateStatus.NOT_INSTALLED and self.installed:
                return UpdateStatus.UP_TO_DATE
            return self.last_result.status
        state = self.updater.state()
        return UpdateStatus.UPDATE_AVAILABLE if state.update_available else None

    @property
    def latest(self) -> str | None:
        if self.last_result and self.last_result.latest:
            return self.last_result.latest
        return self._ctx.settings.settings.ytdlp_latest_known or None

    @property
    def busy(self) -> bool:
        return self.phase != Phase.IDLE

    def _set(self, phase: Phase) -> None:
        self.phase = phase
        self.changed.emit()

    # --------------------------------------------------------------- startup
    def start(self) -> None:
        """Read the installed version, then (only if due) check the official source — discreetly."""
        self._set(Phase.READING)
        run_in_background(lambda: self.updater.state(refresh_version=True), self._startup_state,
                          lambda exc: self._fail_quietly(exc))

    def _startup_state(self, state) -> None:
        self.installed, self.path = state.installed, state.path
        self._set(Phase.IDLE)
        if self.installed is None:
            if state.path is not None:
                self.error = "O yt-dlp foi encontrado, mas não respondeu. Tente reinstalá-lo."
            self.install_offered.emit()
        elif self.updater.should_auto_check():
            self.check(manual=False)

    def _fail_quietly(self, exc: BaseException) -> None:
        log.warning("Could not read yt-dlp state: %s", exc)
        self._set(Phase.IDLE)

    def refresh_version(self) -> None:
        run_in_background(lambda: self.updater.state(refresh_version=True), self._refreshed, self._fail_quietly)

    def _refreshed(self, state) -> None:
        self.installed, self.path = state.installed, state.path
        self.changed.emit()

    # ----------------------------------------------------------------- check
    def check(self, manual: bool = True) -> bool:
        if self.busy:
            return False
        self.error = None
        self._set(Phase.CHECKING)
        run_in_background(self.updater.check, lambda result: self._checked(result, manual),
                          lambda exc: self._checked(UpdateCheck(UpdateStatus.CHECK_FAILED, self.installed, None,
                                                                str(exc)), manual))
        return True

    def _checked(self, result: UpdateCheck, manual: bool) -> None:
        self.last_result = result
        if result.installed:
            self.installed = result.installed
        self.error = result.error if result.status == UpdateStatus.CHECK_FAILED else None
        self._set(Phase.IDLE)
        if result.status == UpdateStatus.UPDATE_AVAILABLE:
            self.update_found.emit(result, manual)
        elif manual and result.status == UpdateStatus.CHECK_FAILED:
            self._ctx.notify("Falha na verificação", result.error or "Não foi possível verificar agora.", "warning")
        elif manual and result.status == UpdateStatus.UP_TO_DATE:
            self._ctx.notify("yt-dlp atualizado", "✓ Você está usando a versão mais recente.", "success")

    # --------------------------------------------------------------- install
    def install(self, manual: bool = True) -> bool:
        """Install/update now. Refused (with a clear message) while downloads are running."""
        if self.busy:
            return False
        if self.updater.is_busy():
            if self._ctx.settings.settings.ytdlp_update_when_idle:
                self._ctx.settings.update(ytdlp_pending_install=True)
            self.blocked.emit(manual)
            return False
        first = self.installed is None
        self.error = None
        self._cancel = threading.Event()
        cancel = self._cancel
        self._set(Phase.DOWNLOADING)
        self.progress.emit(0, None)
        run_in_background(
            lambda: self.updater.install(lambda phase, done, total: self._progress_from_thread.emit(phase, done,
                                                                                                    total), cancel),
            lambda version: self._installed(version, first), lambda exc: self._install_failed(exc, first))
        return True

    def cancel(self) -> None:
        if self._cancel is not None and self.phase == Phase.DOWNLOADING:
            self._cancel.set()

    def _on_thread_progress(self, phase: str, done: int, total: object) -> None:
        if phase == "download":
            self.progress.emit(done, total)
        elif self.phase != Phase.INSTALLING:
            self._set(Phase.INSTALLING)

    def _installed(self, version: str, first: bool) -> None:
        self._cancel = None
        self.installed = version
        self.path = self.updater.manager.get_executable_path()
        self.last_result = UpdateCheck(UpdateStatus.UP_TO_DATE, version, version)
        self._set(Phase.IDLE)
        message = f"Versão: {version}"
        self._ctx.notify("✓ yt-dlp instalado com sucesso" if first else "✓ yt-dlp atualizado", message, "success")
        self.finished.emit(True, message, first)

    def _install_failed(self, exc: BaseException, first: bool) -> None:
        self._cancel = None
        self._set(Phase.IDLE)
        if isinstance(exc, UpdateBlocked):
            self.blocked.emit(True)
            return
        message = exc.message if isinstance(exc, YtDlpError) else \
            "Não foi possível atualizar o yt-dlp. A versão atual foi mantida."
        if not isinstance(exc, UpdateCancelled):
            self.error = message
        self.refresh_version()  # whatever happened, show the version that really answers now
        current = self.installed  # the previous version keeps working (never replaced by a bad file)
        detail = f"{message}\n\nVersão atual: {current}" if current and not first else message
        self._ctx.notify("Atualização cancelada" if isinstance(exc, UpdateCancelled) else
                         "! Não foi possível atualizar", detail,
                         "info" if isinstance(exc, UpdateCancelled) else "error")
        self.finished.emit(False, detail, first)

    # ----------------------------------------------------------- when idle
    def _on_event(self, name: str, _payload: object) -> None:
        if name != ev.QUEUE_CHANGED or not isValid(self):
            return
        settings = self._ctx.settings.settings
        if settings.ytdlp_pending_install and settings.ytdlp_update_when_idle and not self.busy \
                and not self.updater.is_busy():
            log.info("Downloads finished: installing the pending yt-dlp update")
            self.install(manual=False)
