"""Wires services together and exposes them to the UI.

One instance per process: the main window, the floating widget, the tray and the notifications all use these same
objects (a single DownloadManager/queue, one database connection, one SettingsManager, one ShortcutManager).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app import APP_VERSION, UPDATE_MANIFEST_URL
from app.core.events import EventBus
from app.core.managers.download_manager import DownloadManager
from app.core.managers.settings_manager import SettingsManager
from app.core.providers.base import DownloadProvider
from app.core.services.analyzer import Analyzer
from app.core.services.history import HistoryRepository
from app.core.services.thumbnails import ThumbnailStore
from app.core.services.tutorial import (
    TUTORIAL_SHORTCUTS_INTRO,
    TUTORIAL_WIDGET,
    TutorialManager,
    TutorialStateRepository,
)
from app.core.services.update_service import UpdateService
from app.core.services.updates import UpdateManager, YtDlpUpdater
from app.core.services.ytdlp_manager import YtDlpManager, default_manager
from app.core.shortcuts import ShortcutManager, ShortcutRepository
from app.infrastructure.database import Database
from app.system.autostart import AutoStart
from app.ui.bridge import DownloadBridge
from app.utils.paths import thumbnails_dir

Notifier = Callable[..., None]  # title, message, tone, action=None


@dataclass
class AppContext:
    db: Database
    settings: SettingsManager
    provider: DownloadProvider
    analyzer: Analyzer
    history: HistoryRepository
    thumbnails: ThumbnailStore
    downloads: DownloadManager
    bridge: DownloadBridge
    updates: UpdateService
    tutorial: TutorialManager
    events: EventBus
    shortcuts: ShortcutManager
    widget_tour_state: TutorialStateRepository
    shortcuts_intro_state: TutorialStateRepository
    autostart: AutoStart
    ytdlp: YtDlpManager
    update_manager: UpdateManager
    _notifier: Notifier | None = field(default=None, repr=False)

    @classmethod
    def create(cls, db: Database, provider: DownloadProvider, autostart: AutoStart | None = None,
               ytdlp: YtDlpManager | None = None) -> AppContext:
        """`ytdlp` defaults to the provider's manager: downloads, analysis and updates share one installation."""
        ytdlp = ytdlp or getattr(provider, "manager", None) or default_manager()
        events = EventBus()
        settings = SettingsManager(db)
        history = HistoryRepository(db)
        thumbnails = ThumbnailStore(thumbnails_dir())
        downloads = DownloadManager(provider, history, lambda: settings.settings, thumbnails, events=events)
        tutorial_repo = TutorialStateRepository(db)
        updates = UpdateService(APP_VERSION, UPDATE_MANIFEST_URL)
        return cls(db=db, settings=settings, provider=provider, analyzer=Analyzer(provider, events), history=history,
                   thumbnails=thumbnails, downloads=downloads, bridge=DownloadBridge(downloads, events),
                   updates=updates,
                   tutorial=TutorialManager(tutorial_repo), events=events,
                   shortcuts=ShortcutManager(ShortcutRepository(db)),
                   widget_tour_state=tutorial_repo.for_key(TUTORIAL_WIDGET),
                   shortcuts_intro_state=tutorial_repo.for_key(TUTORIAL_SHORTCUTS_INTRO),
                   autostart=autostart or AutoStart(), ytdlp=ytdlp,
                   update_manager=UpdateManager(updates, YtDlpUpdater(ytdlp, settings, downloads.has_running_work)))

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    def notify(self, title: str, message: str, tone: str = "info", action: tuple[str, Callable[[], None]] | None = None) -> None:
        """`action` = (button label, callback), e.g. ("Abrir arquivo", ...)."""
        if self._notifier:
            self._notifier(title, message, tone, action)
