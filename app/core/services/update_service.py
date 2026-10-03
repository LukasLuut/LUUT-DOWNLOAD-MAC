"""Update checks against an optional JSON manifest.

Manifest format: {"version": "1.1.0", "url": "https://...", "changelog": "..."}.
The app never depends on this: without a configured URL, or offline, `check()` returns None.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from app.infrastructure.logger import get_logger
from app.utils.urls import normalize_url

log = get_logger("updates")

Fetcher = Callable[[str], bytes]


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    download_url: str | None
    changelog: str

    @property
    def is_newer(self) -> bool:
        return parse_version(self.latest_version) > parse_version(self.current_version)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:4]) or (0,)


def _http_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - validated http(s) URL
        return response.read(256 * 1024)


class UpdateService:
    def __init__(self, current_version: str, manifest_url: str = "", fetcher: Fetcher = _http_fetch) -> None:
        self.current_version = current_version
        self.manifest_url = normalize_url(manifest_url) if manifest_url else None
        self._fetch = fetcher

    @property
    def is_configured(self) -> bool:
        return self.manifest_url is not None

    def check(self) -> UpdateInfo | None:
        if not self.manifest_url:
            return None
        try:
            data = json.loads(self._fetch(self.manifest_url).decode("utf-8"))
            latest = str(data["version"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.info("Update check failed: %s", exc)
            return None
        return UpdateInfo(self.current_version, latest, normalize_url(data.get("url")),
                          str(data.get("changelog") or ""))
