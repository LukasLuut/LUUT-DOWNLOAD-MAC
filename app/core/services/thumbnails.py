"""Thumbnail download and local cache (so history works offline)."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from app import APP_NAME, APP_VERSION
from app.infrastructure.logger import get_logger
from app.utils.urls import normalize_url

log = get_logger("thumbnails")
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024


def fetch_thumbnail(url: str | None, timeout: float = 10) -> bytes | None:
    """Download a thumbnail image; returns None on any failure (thumbnails are optional)."""
    safe_url = normalize_url(url)
    if not safe_url:
        return None
    request = urllib.request.Request(safe_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - http(s) only
            data = response.read(MAX_THUMBNAIL_BYTES + 1)
    except (OSError, ValueError) as exc:
        log.info("Thumbnail unavailable: %s", exc)
        return None
    if not data or len(data) > MAX_THUMBNAIL_BYTES:
        return None
    return data


class ThumbnailStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def save(self, task_id: str, data: bytes | None) -> str | None:
        if not data:
            return None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{task_id}.img"
            path.write_bytes(data)
        except OSError as exc:
            log.warning("Could not cache thumbnail: %s", exc)
            return None
        return str(path)

    def load(self, path: str | None) -> bytes | None:
        if not path:
            return None
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def delete(self, path: str | None) -> None:
        if not path:
            return
        candidate = Path(path)
        if candidate.parent.resolve() == self._dir.resolve():
            candidate.unlink(missing_ok=True)
