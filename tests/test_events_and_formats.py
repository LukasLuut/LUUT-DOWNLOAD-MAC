"""EventBus, DownloadManager/Analyzer events and the extra download types (video only, M4A, original audio)."""

import threading

import pytest

from app.core import events as ev
from app.core.errors import AppError
from app.core.events import EventBus
from app.core.models.download import DownloadStatus, request_from_info
from app.core.models.video import (
    CONTAINER_AUDIO,
    CONTAINER_AUDIO_M4A,
    CONTAINER_AUDIO_ORIGINAL,
    CONTAINER_MP4,
    CONTAINER_VIDEO_MP4,
    CONTAINER_VIDEO_ORIGINAL,
    KIND_AUDIO,
    KIND_AV,
    KIND_VIDEO,
    container_kind,
    format_label,
)
from app.core.providers.base import DownloadJob
from app.core.providers.ytdlp_provider import (
    FormatCatalog,
    YtDlpProvider,
    build_format_options,
)
from app.core.services.analyzer import Analyzer
from app.infrastructure.logger import mask_urls
from tests.conftest import FakeProvider, make_request, wait_until
from tests.test_engine_and_services import SAMPLE_INFO


class Recorder:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        bus.subscribe("*", self._on)

    def _on(self, name, payload) -> None:
        with self._lock:
            self.events.append((name, payload))

    def names(self, exclude=(ev.DOWNLOAD_PROGRESS, ev.QUEUE_CHANGED)) -> list[str]:
        with self._lock:
            return [n for n, _ in self.events if n not in exclude]


def test_bus_rejects_unknown_events_and_isolates_failures():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.subscribe("download_exploded", lambda *_: None)
    seen = []
    bus.subscribe(ev.QUEUE_CHANGED, lambda *_: 1 / 0)
    unsubscribe = bus.subscribe(ev.QUEUE_CHANGED, lambda name, payload: seen.append(payload))
    bus.publish(ev.QUEUE_CHANGED, 1)
    unsubscribe()
    bus.publish(ev.QUEUE_CHANGED, 2)
    assert seen == [1]


def test_download_lifecycle_events(make_manager, settings_manager):
    manager = make_manager()
    recorder = Recorder(manager.events)
    task = manager.add(make_request(settings_manager, "ev1"))
    assert wait_until(lambda: manager.get(task.id).status == DownloadStatus.COMPLETED)
    names = recorder.names()
    assert names[0] == ev.DOWNLOAD_ADDED
    assert ev.DOWNLOAD_STARTED in names and names[-1] == ev.DOWNLOAD_COMPLETED
    all_names = [n for n, _ in recorder.events]
    assert ev.DOWNLOAD_PROGRESS in all_names and ev.QUEUE_CHANGED in all_names
    manager.remove(task.id)
    assert recorder.names()[-1] == ev.DOWNLOAD_REMOVED


def test_pause_resume_cancel_events(make_manager, settings_manager, provider):
    provider.gate.clear()
    manager = make_manager()
    recorder = Recorder(manager.events)
    task = manager.add(make_request(settings_manager, "ev2"))
    assert wait_until(lambda: provider.running == 1)
    manager.pause(task.id)
    provider.gate.set()
    assert wait_until(lambda: manager.get(task.id).status == DownloadStatus.PAUSED)
    manager.resume(task.id)
    provider.gate.clear()
    assert wait_until(lambda: provider.running == 1)
    manager.cancel(task.id)
    provider.gate.set()
    assert wait_until(lambda: manager.get(task.id).status == DownloadStatus.CANCELLED)
    names = recorder.names()
    for expected in (ev.DOWNLOAD_PAUSED, ev.DOWNLOAD_RESUMED, ev.DOWNLOAD_CANCELLED):
        assert expected in names
    assert names.index(ev.DOWNLOAD_PAUSED) < names.index(ev.DOWNLOAD_RESUMED) < names.index(ev.DOWNLOAD_CANCELLED)


def test_failed_event(make_manager, settings_manager, provider):
    from app.core.errors import ErrorKind

    manager = make_manager()
    recorder = Recorder(manager.events)
    provider.failures["https://example.com/bad"] = [ErrorKind.UNAVAILABLE]
    task = manager.add(make_request(settings_manager, "bad"))
    assert wait_until(lambda: manager.get(task.id).status == DownloadStatus.FAILED)
    assert recorder.names()[-1] == ev.DOWNLOAD_FAILED


def test_analysis_events_carry_no_url():
    bus = EventBus()
    recorder = Recorder(bus)
    analyzer = Analyzer(FakeProvider(), bus)
    analyzer.analyze("https://example.com/private-link", with_thumbnail=False, source="widget")
    with pytest.raises(AppError):
        analyzer.analyze("https://example.com/invalid", with_thumbnail=False)
    names = [n for n, _ in recorder.events]
    assert names == [ev.ANALYSIS_STARTED, ev.ANALYSIS_COMPLETED, ev.ANALYSIS_STARTED, ev.ANALYSIS_FAILED]
    assert all("example.com" not in repr(payload) for _, payload in recorder.events)  # (title is fake data)
    assert recorder.events[0][1].source == "widget"


def test_mask_urls_keeps_only_the_host():
    text = mask_urls("ERROR: Unsupported URL: https://user:pw@www.site.com/watch?v=abc123&t=1 (x)")
    assert "abc123" not in text and "pw" not in text and "https://www.site.com/…" in text


# ------------------------------------------------------------------------ formats
def test_catalog_offers_video_only_and_audio_formats_with_ffmpeg():
    catalog = FormatCatalog(SAMPLE_INFO, has_ffmpeg=True)
    assert catalog.video_only_containers() == [CONTAINER_VIDEO_MP4, CONTAINER_VIDEO_ORIGINAL]
    assert [q.height for q in catalog.video_only_qualities()] == [0, 2160, 1080]
    assert catalog.audio_containers() == [CONTAINER_AUDIO_M4A, CONTAINER_AUDIO_ORIGINAL]
    assert catalog.audio_original_format() == "M4A"


def test_catalog_without_ffmpeg_offers_only_what_needs_no_conversion():
    catalog = FormatCatalog(SAMPLE_INFO, has_ffmpeg=False)
    assert catalog.audio_containers() == []  # "audio" already is the original M4A stream
    assert catalog.video_only_containers() == [CONTAINER_VIDEO_MP4, CONTAINER_VIDEO_ORIGINAL]
    progressive = {"formats": [{"ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a"}]}
    assert FormatCatalog(progressive, False).video_only_containers() == []  # never pretends to strip audio


def test_video_info_groups_options_by_kind():
    info = YtDlpProvider(ffmpeg_path="ffmpeg")._to_video_info("https://x.com", SAMPLE_INFO)
    assert info.kinds() == [KIND_AV, KIND_VIDEO, KIND_AUDIO]
    assert info.containers_for(KIND_AUDIO) == [CONTAINER_AUDIO, CONTAINER_AUDIO_M4A, CONTAINER_AUDIO_ORIGINAL]
    assert [format_label(c, info) for c in info.containers_for(KIND_AUDIO)] == ["MP3", "M4A", "Original (M4A)"]
    assert [format_label(c, info) for c in info.containers_for(KIND_VIDEO)] == ["MP4", "Original (WEBM)"]
    assert info.qualities_for(KIND_AUDIO) == []
    assert info.estimated_size_for(1080, CONTAINER_VIDEO_MP4) == 50_000  # video stream only
    assert info.estimated_size_for(1080, CONTAINER_MP4) == 51_000       # video + audio


def test_request_from_info_is_shared_by_every_interface():
    info = YtDlpProvider(ffmpeg_path="ffmpeg")._to_video_info("https://x.com/v", SAMPLE_INFO)
    audio = request_from_info(info, "C:/out", CONTAINER_AUDIO_M4A, 1080)
    assert audio.quality_height == 0 and audio.container == CONTAINER_AUDIO_M4A
    video = request_from_info(info, "C:/out", CONTAINER_VIDEO_ORIGINAL, 1080)
    assert video.quality_height == 1080 and container_kind(video.container) == KIND_VIDEO


@pytest.mark.parametrize("container,ffmpeg,expected_format,postprocessor", [
    (CONTAINER_VIDEO_MP4, True, "bv[height<=?720]", "FFmpegVideoRemuxer"),
    (CONTAINER_VIDEO_MP4, False, "bv[height<=?720][ext=mp4]", None),
    (CONTAINER_VIDEO_ORIGINAL, True, "bv[height<=?720]", None),
    (CONTAINER_AUDIO_M4A, True, "ba[ext=m4a]/ba", "FFmpegExtractAudio"),
    (CONTAINER_AUDIO_ORIGINAL, True, "ba", None),
])
def test_format_selection_for_new_types(container, ffmpeg, expected_format, postprocessor):
    opts = build_format_options(DownloadJob("u", 720, container), ffmpeg)
    assert opts["format"] == expected_format
    keys = [p["key"] for p in opts.get("postprocessors", [])]
    assert keys == ([postprocessor] if postprocessor else [])
    assert "+" not in opts["format"]  # video-only/audio-only never merge streams


def test_task_labels_for_new_types():
    from app.core.models.download import DownloadTask

    task = DownloadTask(url="u", output_dir="o", container=CONTAINER_AUDIO_ORIGINAL)
    assert task.quality_label == "Áudio"
    assert DownloadTask(url="u", output_dir="o", container=CONTAINER_VIDEO_MP4, quality_height=720).quality_label == "720p"
