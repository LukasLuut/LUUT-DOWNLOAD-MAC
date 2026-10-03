"""Update policies (Qt-free). Kept apart from the mechanics so the app itself can get an updater later:

    UpdateManager
    ├── app    -> UpdateService (Luut Video Downloader; optional manifest, not active yet)
    └── ytdlp  -> YtDlpUpdater  (yt-dlp.exe through YtDlpManager)
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.managers.settings_manager import SettingsManager
from app.core.services.update_service import UpdateService
from app.core.services.ytdlp_manager import (
    Progress, UpdateCheck, UpdateStatus, YtDlpError, YtDlpManager, compare_versions, parse_version,
)
from app.infrastructure.logger import get_logger

log = get_logger("updates")

CHECK_INTERVAL = timedelta(hours=24)
BUSY_MESSAGE = ("Não é possível atualizar o yt-dlp agora. Existem downloads em andamento. A atualização poderá ser "
                "realizada quando todos os downloads terminarem.")


class UpdateBlocked(YtDlpError):
    def __init__(self) -> None:
        super().__init__(BUSY_MESSAGE, "Downloads in progress")


@dataclass(frozen=True)
class YtDlpState:
    installed: str | None
    path: Path | None
    last_check: str | None
    latest_known: str | None

    @property
    def update_available(self) -> bool:
        if not (self.installed and self.latest_known) or parse_version(self.latest_known) is None:
            return False
        return compare_versions(self.latest_known, self.installed) > 0


class YtDlpUpdater:
    def __init__(self, manager: YtDlpManager, settings: SettingsManager, busy: Callable[[], bool],
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.manager = manager
        self._settings = settings
        self._busy = busy
        self._clock = clock
        self._lock = threading.Lock()
        self._dismissed_this_session: set[str] = set()

    # ------------------------------------------------------------ state
    def state(self, refresh_version: bool = False) -> YtDlpState:
        settings = self._settings.settings
        return YtDlpState(self.manager.get_installed_version(refresh=refresh_version),
                          self.manager.get_executable_path(), settings.ytdlp_last_check or None,
                          settings.ytdlp_latest_known or None)

    def last_check(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self._settings.settings.ytdlp_last_check)
        except ValueError:
            return None

    def should_auto_check(self) -> bool:
        if not self._settings.settings.ytdlp_auto_check:
            return False
        last = self.last_check()
        return last is None or self._clock() - last >= CHECK_INTERVAL or last > self._clock()

    def is_busy(self) -> bool:
        return self._busy() or self.manager.in_use

    # ------------------------------------------------------------ actions
    def check(self) -> UpdateCheck:
        """Always asks the official source (the manual button ignores the 24 h interval)."""
        result = self.manager.check_for_updates()
        if result.status != UpdateStatus.CHECK_FAILED:  # a failed check is retried at the next opportunity
            self._settings.update(ytdlp_last_check=self._clock().isoformat(timespec="seconds"),
                                  ytdlp_latest_known=result.latest or "")
        return result

    def install(self, progress: Progress | None = None, cancel: threading.Event | None = None,
                check: UpdateCheck | None = None) -> str:
        """Never while something is downloading. Re-checks the latest version unless a fresh check is given."""
        if not self._lock.acquire(blocking=False):
            raise YtDlpError("Uma atualização do yt-dlp já está em andamento.", "Concurrent install")
        try:
            if self.is_busy():
                raise UpdateBlocked()
            release = check.release if check and check.release else None
            version = self.manager.install(release, progress, cancel)
            self._settings.update(ytdlp_latest_known=version, ytdlp_last_check=self._clock().isoformat(
                timespec="seconds"), ytdlp_pending_install=False)
            return version
        finally:
            self._lock.release()

    def dismiss(self, version: str) -> None:
        """"Depois": not asked again this session; remembered for the automatic checks."""
        self._dismissed_this_session.add(version)
        self._settings.update(ytdlp_dismissed_version=version)
        log.info("yt-dlp update %s postponed by the user", version)

    def is_dismissed(self, version: str | None) -> bool:
        return bool(version) and (version in self._dismissed_this_session
                                  or self._settings.settings.ytdlp_dismissed_version == version)


class UpdateManager:
    def __init__(self, app: UpdateService, ytdlp: YtDlpUpdater) -> None:
        self.app = app
        self.ytdlp = ytdlp
