"""Real downloads through yt-dlp + ffmpeg. Opt-in: set LUUT_NETWORK_TESTS=1."""

import os
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorKind
from app.core.managers.download_manager import DownloadManager
from app.core.models.download import DownloadRequest, DownloadStatus
from app.core.models.video import CONTAINER_AUDIO, CONTAINER_MP4
from app.core.providers.ytdlp_provider import YtDlpProvider
from app.core.services.analyzer import Analyzer
from app.core.services.history import HistoryRepository
from tests.conftest import wait_until

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(os.environ.get("LUUT_NETWORK_TESTS") != "1", reason="network tests disabled"),
]

SHORT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s
LONG_VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny (Blender Foundation, CC-BY)
DIRECT_FILE = "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"


@pytest.fixture
def real_manager(db, settings_manager, tmp_path):
    manager = DownloadManager(YtDlpProvider(), HistoryRepository(db), lambda: settings_manager.settings)
    yield manager
    manager.shutdown(cancel=True, timeout=20)


def test_analyze_real_video():
    result = Analyzer(YtDlpProvider()).analyze(SHORT_VIDEO)
    assert result.info.title and result.info.duration
    assert result.thumbnail
    assert result.info.qualities[0].height == 0


def test_analyze_invalid_real_url():
    with pytest.raises(AppError) as err:
        Analyzer(YtDlpProvider()).analyze("https://example.com/not-a-video")
    assert err.value.kind in (ErrorKind.UNSUPPORTED, ErrorKind.UNAVAILABLE)


def test_full_download_with_merge(real_manager, settings_manager):
    info = Analyzer(YtDlpProvider()).analyze(SHORT_VIDEO, with_thumbnail=False).info
    task = real_manager.add(DownloadRequest(url=info.url, output_dir=settings_manager.settings.default_dir,
                                            title=info.title, container=CONTAINER_MP4))
    assert wait_until(lambda: real_manager.get(task.id).status.is_terminal, 180)
    done = real_manager.get(task.id)
    assert done.status == DownloadStatus.COMPLETED, done.error_message
    path = Path(done.file_path)
    assert path.suffix == ".mp4" and path.stat().st_size > 100_000


def test_pause_resume_and_cancel_real(real_manager, settings_manager):
    task = real_manager.add(DownloadRequest(url=LONG_VIDEO, output_dir=settings_manager.settings.default_dir,
                                            title="Big Buck Bunny", quality_height=720, container=CONTAINER_MP4))
    assert wait_until(lambda: real_manager.get(task.id).progress.downloaded_bytes > 2_000_000, 120)
    assert real_manager.pause(task.id)
    assert wait_until(lambda: real_manager.get(task.id).status == DownloadStatus.PAUSED, 60)
    paused = real_manager.get(task.id)
    assert paused.has_partial_data
    downloaded_before = paused.progress.downloaded_bytes
    assert real_manager.resume(task.id)
    assert wait_until(lambda: real_manager.get(task.id).progress.downloaded_bytes > downloaded_before, 120)
    assert real_manager.cancel(task.id)
    assert wait_until(lambda: real_manager.get(task.id).status == DownloadStatus.CANCELLED, 60)
    assert not real_manager.work_dir(task.id).exists()


def test_direct_file_download(real_manager, settings_manager):
    info = Analyzer(YtDlpProvider()).analyze(DIRECT_FILE, with_thumbnail=False).info
    task = real_manager.add(DownloadRequest(url=info.url, output_dir=settings_manager.settings.default_dir,
                                            title="Big Buck Bunny 320", container=info.containers[0]))
    assert wait_until(lambda: real_manager.get(task.id).status.is_terminal, 300)
    done = real_manager.get(task.id)
    assert done.status == DownloadStatus.COMPLETED, done.error_message


def test_audio_only_download_real(real_manager, settings_manager):
    info = Analyzer(YtDlpProvider()).analyze(SHORT_VIDEO, with_thumbnail=False).info
    assert CONTAINER_AUDIO in info.containers and info.audio_format == "MP3"
    task = real_manager.add(DownloadRequest(url=info.url, output_dir=settings_manager.settings.default_dir,
                                            title=info.title, container=CONTAINER_AUDIO))
    assert wait_until(lambda: real_manager.get(task.id).status.is_terminal, 180)
    done = real_manager.get(task.id)
    assert done.status == DownloadStatus.COMPLETED, done.error_message
    path = Path(done.file_path)
    assert path.suffix == ".mp3" and path.stat().st_size > 50_000
    assert list(path.parent.iterdir()) == [path]  # no leftover video/partial files


def test_reveal_in_folder_organized_path(real_manager, settings_manager, tmp_path):
    """Organized subfolders: the stored file path is the real final location."""
    settings_manager.update(organize_enabled=True, organize_pattern="year_month")
    info = Analyzer(YtDlpProvider()).analyze(DIRECT_FILE, with_thumbnail=False).info
    task = real_manager.add(DownloadRequest(url=info.url, output_dir=settings_manager.settings.default_dir,
                                            title="Organizado", container=info.containers[0]))
    assert wait_until(lambda: real_manager.get(task.id).status.is_terminal, 300)
    path = Path(real_manager.get(task.id).file_path)
    assert path.is_file()
    assert path.parent.parent.parent == Path(settings_manager.settings.default_dir).resolve()
