import errno
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorKind, to_app_error
from app.core.models.video import CONTAINER_AUDIO, CONTAINER_MP4, CONTAINER_ORIGINAL
from app.core.providers.base import DownloadJob
from app.core.providers.ytdlp_provider import (
    FormatCatalog, YtDlpProvider, _ProgressTracker, build_format_options, classify_engine_error,
)
from app.core.services.analyzer import Analyzer
from app.core.services.update_service import UpdateService, parse_version
from app.infrastructure.filesystem import move_to_destination, organized_subdir
from app.infrastructure.logger import redact
from tests.conftest import FakeProvider

SAMPLE_INFO = {
    "title": "Sample",
    "formats": [
        {"format_id": "sb", "ext": "mhtml", "protocol": "mhtml", "height": 90, "vcodec": "none", "acodec": "none"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 1_000, "tbr": 128},
        {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 5_000, "tbr": 500},
        {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 50_000, "tbr": 4000},
        {"format_id": "313", "ext": "webm", "height": 2160, "vcodec": "vp9", "acodec": "none", "filesize": 200_000, "tbr": 16000},
        {"format_id": "drm", "ext": "mp4", "height": 4320, "vcodec": "avc1", "acodec": "none", "has_drm": True},
        {"format_id": "240", "ext": "mp4", "height": 240, "vcodec": "avc1", "acodec": "none", "filesize": 900},
    ],
}


class TestFormatCatalog:
    def test_lists_only_real_qualities(self):
        catalog = FormatCatalog(SAMPLE_INFO, has_ffmpeg=True)
        assert [q.height for q in catalog.qualities()] == [0, 2160, 1080, 360]
        assert catalog.estimate(1080) == 51_000
        assert catalog.qualities()[0].estimated_size == 201_000

    def test_without_ffmpeg_only_progressive(self):
        catalog = FormatCatalog(SAMPLE_INFO, has_ffmpeg=False)
        assert [q.height for q in catalog.qualities()] == [0, 360]
        assert catalog.containers() == [CONTAINER_MP4, CONTAINER_AUDIO]
        assert catalog.audio_format() == "M4A"

    def test_containers(self):
        assert FormatCatalog(SAMPLE_INFO, True).containers() == [CONTAINER_MP4, CONTAINER_ORIGINAL, CONTAINER_AUDIO]
        only_webm = {"formats": [{"ext": "webm", "height": 720, "vcodec": "vp9", "acodec": "opus"}]}
        assert FormatCatalog(only_webm, False).containers() == [CONTAINER_ORIGINAL]

    def test_audio_only_estimate(self):
        catalog = FormatCatalog(SAMPLE_INFO, has_ffmpeg=True)
        assert catalog.audio_format() == "MP3"
        assert catalog.audio_estimate(60) == 60 * 192_000 // 8
        assert catalog.audio_estimate(None) is None
        assert FormatCatalog(SAMPLE_INFO, has_ffmpeg=False).audio_estimate(60) == 1_000

    def test_direct_file_without_metadata(self):
        catalog = FormatCatalog({"url": "https://x.com/a.mp4", "ext": "mp4"}, True)
        assert [q.height for q in catalog.qualities()] == [0]
        assert catalog.qualities()[0].estimated_size is None
        assert catalog.containers() == [CONTAINER_ORIGINAL, CONTAINER_AUDIO]

    def test_to_video_info_handles_missing_metadata(self):
        info = YtDlpProvider(ffmpeg_path="ffmpeg")._to_video_info("https://x.com", {"formats": SAMPLE_INFO["formats"]})
        assert info.title is None and info.duration is None and info.uploader is None
        assert info.max_height == 2160

    def test_drm_only_content_is_rejected(self):
        with pytest.raises(AppError) as err:
            YtDlpProvider(ffmpeg_path="ffmpeg")._to_video_info("https://x.com", {"formats": [{"has_drm": True}]})
        assert err.value.kind == ErrorKind.DRM


class TestFormatSelection:
    def test_best_mp4_with_ffmpeg(self):
        opts = build_format_options(DownloadJob("u", 0, CONTAINER_MP4), True)
        assert opts["format"] == "bv*+ba/b"
        assert opts["merge_output_format"] == "mp4"

    def test_height_limited_original(self):
        opts = build_format_options(DownloadJob("u", 720, CONTAINER_ORIGINAL), True)
        assert opts["format"] == "bv*[height<=?720]+ba/b[height<=?720]"
        assert "merge_output_format" not in opts

    def test_audio_only_with_ffmpeg_extracts_mp3(self):
        opts = build_format_options(DownloadJob("u", 1080, CONTAINER_AUDIO), True)
        assert opts["format"] == "ba/b"
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"

    def test_audio_only_without_ffmpeg_keeps_original_stream(self):
        opts = build_format_options(DownloadJob("u", 0, CONTAINER_AUDIO), False)
        assert opts["format"] == "ba[ext=m4a]/ba" and "postprocessors" not in opts

    def test_without_ffmpeg_never_merges(self):
        opts = build_format_options(DownloadJob("u", 1080, CONTAINER_MP4), False)
        assert "+" not in opts["format"]


def test_progress_tracker_combines_streams():
    updates = []
    tracker = _ProgressTracker(updates.append)
    requested = {"requested_formats": [{"filesize": 800}, {"filesize": 200}]}
    tracker.update({"status": "downloading", "filename": "v", "downloaded_bytes": 400, "total_bytes": 800,
                    "speed": 100, "info_dict": requested})
    tracker.update({"status": "finished", "filename": "v", "downloaded_bytes": 800, "total_bytes": 800,
                    "info_dict": requested})
    tracker.update({"status": "finished", "filename": "a", "downloaded_bytes": 200, "total_bytes": 200,
                    "info_dict": requested})
    assert updates[0].total_bytes == 1000 and updates[0].fraction == 0.4
    assert updates[-1].fraction == 1.0


def test_progress_tracker_unknown_total_is_not_faked():
    updates = []
    tracker = _ProgressTracker(updates.append)
    tracker.update({"status": "downloading", "filename": "v", "downloaded_bytes": 10,
                    "info_dict": {"requested_formats": [{}, {}]}})
    assert updates[0].total_bytes is None and updates[0].fraction is None


@pytest.mark.parametrize("message,kind", [
    ("ERROR: Unsupported URL: https://example.com", ErrorKind.UNSUPPORTED),
    ("ERROR: [youtube] abc: Private video. Sign in", ErrorKind.UNAVAILABLE),
    ("ERROR: Unable to download webpage: HTTP Error 404: Not Found", ErrorKind.UNAVAILABLE),
    ("ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>", ErrorKind.NETWORK),
    ("ERROR: The read operation timed out", ErrorKind.NETWORK),
    ("ERROR: This video is DRM protected", ErrorKind.DRM),
    ("ERROR: [Errno 28] No space left on device", ErrorKind.DISK_FULL),
    ("Something odd", ErrorKind.UNEXPECTED),
])
def test_engine_error_classification(message, kind):
    assert classify_engine_error(Exception(message)).kind == kind


def test_os_error_classification():
    assert to_app_error(OSError(errno.ENOSPC, "full")).kind == ErrorKind.DISK_FULL
    assert to_app_error(PermissionError(errno.EACCES, "no")).kind == ErrorKind.PERMISSION
    assert to_app_error(ValueError("x")).user_message.startswith("Ocorreu um erro inesperado")


def test_analyzer_rejects_invalid_and_empty_urls():
    analyzer = Analyzer(FakeProvider())
    for bad in ("", "   ", "isso não é link", "ftp://x.com/a"):
        with pytest.raises(AppError) as err:
            analyzer.analyze(bad)
        assert err.value.kind == ErrorKind.INVALID_URL
    with pytest.raises(AppError) as err:
        analyzer.analyze("https://example.com/invalid")
    assert err.value.user_message == "Não foi possível analisar este link."


def test_analyzer_success_without_thumbnail():
    result = Analyzer(FakeProvider()).analyze("https://example.com/ok", with_thumbnail=False)
    assert result.info.title == "Video ok" and result.thumbnail is None


def test_update_service():
    assert parse_version("1.10.0") > parse_version("1.9.9")
    assert UpdateService("1.0.0").check() is None
    manifest = json.dumps({"version": "1.2.0", "url": "https://luut.example/dl", "changelog": "Novidades"}).encode()
    info = UpdateService("1.0.0", "https://luut.example/manifest.json", fetcher=lambda url: manifest).check()
    assert info.is_newer and info.changelog == "Novidades"

    def offline(url):
        raise OSError("offline")

    assert UpdateService("1.0.0", "https://luut.example/m.json", fetcher=offline).check() is None


def test_redaction_of_secrets():
    text = redact("GET https://user:pw@x.com/v?id=1&token=abc123&signature=zzz Authorization: Bearer")
    assert "abc123" not in text and "zzz" not in text and "pw@" not in text
    assert "id=1" in text


def test_organized_subdir(tmp_path: Path):
    when = datetime(2026, 9, 22)
    assert organized_subdir(tmp_path, "year_month", when, None, None) == (tmp_path / "2026" / "Setembro").resolve()
    assert organized_subdir(tmp_path, "site", when, None, "Youtube:tab").name == "Youtube"
    assert organized_subdir(tmp_path, "uploader", when, "../../evil", None).parent == tmp_path.resolve()


def test_move_to_destination_unique(tmp_path: Path):
    for i in range(2):
        src = tmp_path / f"media{i}.mp4"
        src.write_bytes(b"x")
        target = move_to_destination(src, tmp_path / "out", "A/B", overwrite=False)
    assert target.name == "A B (1).mp4"


def test_explorer_select_command_keeps_path_quoted(tmp_path: Path):
    from app.infrastructure.filesystem import explorer_select_command

    target = tmp_path / "Pasta com espaço" / "2026" / "Setembro" / "Meu vídeo (1).mp4"
    command = explorer_select_command(target)
    assert command == f'explorer /select,"{target.resolve()}"'


def test_audio_task_labels():
    from app.core.models.download import DownloadTask

    task = DownloadTask(url="https://x.com/v", output_dir="C:/out", container=CONTAINER_AUDIO, quality_height=0)
    assert task.quality_label == "Áudio"
    assert task.container_label == "Somente áudio"
    task.file_path = "C:/out/Música.mp3"
    assert task.container_label == "MP3"
