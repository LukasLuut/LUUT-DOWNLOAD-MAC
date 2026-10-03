"""URL analysis: validation + engine metadata + thumbnail. Runs in background threads."""

from __future__ import annotations

from dataclasses import dataclass

from app.core import events as ev
from app.core.errors import AppError, ErrorKind, to_app_error
from app.core.events import EventBus
from app.core.models.video import VideoInfo
from app.core.providers.base import DownloadProvider
from app.core.services.thumbnails import fetch_thumbnail
from app.infrastructure.logger import get_logger, mask_urls
from app.utils.urls import normalize_url

log = get_logger("analyzer")


@dataclass
class AnalysisResult:
    info: VideoInfo
    thumbnail: bytes | None


@dataclass(frozen=True)
class AnalysisEvent:
    """Payload of analysis_* events. Carries no URL (it may have come from the clipboard)."""

    request_id: int
    source: str
    title: str | None = None
    error: str | None = None


class Analyzer:
    """Shared by every interface. URLs are never written to the logs."""

    def __init__(self, provider: DownloadProvider, events: EventBus | None = None) -> None:
        self._provider = provider
        self.events = events or EventBus()
        self._counter = 0

    def analyze(self, url: str, with_thumbnail: bool = True, source: str = "app") -> AnalysisResult:
        """Raises AppError with a friendly message; never lets engine exceptions escape."""
        self._counter += 1
        request_id = self._counter
        normalized = normalize_url(url)
        if normalized is None:
            self.events.publish(ev.ANALYSIS_FAILED, AnalysisEvent(request_id, source, error=ErrorKind.INVALID_URL.value))
            raise AppError(ErrorKind.INVALID_URL, "Invalid URL")
        log.info("Analysis started (%s)", source)
        self.events.publish(ev.ANALYSIS_STARTED, AnalysisEvent(request_id, source))
        try:
            info = self._provider.analyze(normalized)
        except Exception as exc:  # noqa: BLE001
            error = to_app_error(exc)
            log.warning("Analysis failed [%s]: %s", error.kind.value, mask_urls(error.detail))
            self.events.publish(ev.ANALYSIS_FAILED, AnalysisEvent(request_id, source, error=error.kind.value))
            raise error from exc
        thumbnail = fetch_thumbnail(info.thumbnail_url) if with_thumbnail else None
        log.info("Analyzed %s: %s (%d qualities)", info.extractor, info.title, len(info.qualities))
        self.events.publish(ev.ANALYSIS_COMPLETED, AnalysisEvent(request_id, source, title=info.title))
        return AnalysisResult(info, thumbnail)
