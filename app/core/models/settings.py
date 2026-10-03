"""User settings with defaults and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from app.utils.paths import default_download_dir, default_temp_dir

EXISTING_RENAME = "rename"
EXISTING_OVERWRITE = "overwrite"
EXISTING_POLICIES = {
    EXISTING_RENAME: "Renomear automaticamente — video (1).mp4",
    EXISTING_OVERWRITE: "Substituir o arquivo existente",
}

ORGANIZE_PATTERNS = {
    "year_month": "Ano / Mês",
    "year": "Ano",
    "uploader": "Canal / Autor",
    "site": "Site de origem",
}

THEMES = {"dark": "Escuro", "light": "Claro"}

WIDGET_POSITIONS = {
    "bottom_right": "Inferior direito",
    "bottom_left": "Inferior esquerdo",
    "top_right": "Superior direito",
    "top_left": "Superior esquerdo",
    "last": "Última posição",
}
WIDGET_OPACITY_MIN, WIDGET_OPACITY_MAX = 70, 100

CONCURRENCY_OPTIONS = (1, 2, 3, 4)
HISTORY_LIMITS = (100, 500, 1000, 5000, 0)  # 0 = unlimited


@dataclass
class AppSettings:
    default_dir: str = ""
    last_dir: str = ""
    max_concurrent: int = 1
    existing_file_policy: str = EXISTING_RENAME
    organize_enabled: bool = False
    organize_pattern: str = "year_month"
    theme: str = "dark"
    start_minimized: bool = False
    minimize_to_tray: bool = False
    notify_completed: bool = True
    notify_errors: bool = True
    history_enabled: bool = True
    history_limit: int = 500
    verbose_logging: bool = False
    temp_dir: str = ""
    suggest_clipboard: bool = True
    notify_started: bool = False
    start_with_windows: bool = False
    # Floating widget (disabled until the user turns it on).
    widget_enabled: bool = False
    widget_start_with_windows: bool = False
    widget_always_on_top: bool = False
    widget_detect_clipboard: bool = False
    widget_minimize_after_start: bool = False
    widget_notifications: bool = True
    widget_position: str = "bottom_right"
    widget_opacity: int = 100
    widget_has_position: bool = False  # widget_x/widget_y hold the last position the user dragged it to
    widget_x: int = 0
    widget_y: int = 0
    widget_compact: bool = False
    # yt-dlp updates (checked automatically every 24 h, never installed without asking by default)
    ytdlp_auto_check: bool = True
    ytdlp_ask_before_update: bool = True
    ytdlp_update_when_idle: bool = False
    ytdlp_pending_install: bool = False
    ytdlp_last_check: str = ""
    ytdlp_latest_known: str = ""
    ytdlp_dismissed_version: str = ""

    def __post_init__(self) -> None:
        self.default_dir = self.default_dir or str(default_download_dir())
        self.temp_dir = self.temp_dir or str(default_temp_dir())
        if self.max_concurrent not in CONCURRENCY_OPTIONS:
            self.max_concurrent = 1
        if self.existing_file_policy not in EXISTING_POLICIES:
            self.existing_file_policy = EXISTING_RENAME
        if self.organize_pattern not in ORGANIZE_PATTERNS:
            self.organize_pattern = "year_month"
        if self.theme not in THEMES:
            self.theme = "dark"
        if self.history_limit not in HISTORY_LIMITS:
            self.history_limit = 500
        if self.widget_position not in WIDGET_POSITIONS:
            self.widget_position = "bottom_right"
        self.widget_opacity = max(WIDGET_OPACITY_MIN, min(WIDGET_OPACITY_MAX, self.widget_opacity))

    @property
    def effective_download_dir(self) -> str:
        return self.last_dir or self.default_dir

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        defaults = cls()
        values: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            default_value = getattr(defaults, f.name)
            value = data[f.name]
            if isinstance(default_value, bool):
                if isinstance(value, bool):
                    values[f.name] = value
            elif isinstance(default_value, int):
                if isinstance(value, int) and not isinstance(value, bool):
                    values[f.name] = value
            elif isinstance(value, str):
                values[f.name] = value
        return cls(**values)
