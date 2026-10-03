from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.errors import AppError, ErrorKind  # noqa: E402
from app.core.managers.download_manager import DownloadManager  # noqa: E402
from app.core.managers.settings_manager import SettingsManager  # noqa: E402
from app.core.models.download import DownloadProgress, DownloadRequest  # noqa: E402
from app.core.models.video import QualityOption, VideoInfo  # noqa: E402
from app.core.providers.base import (  # noqa: E402
    DownloadJob, DownloadProvider, DownloadStopped, EngineInfo, Stage, StopToken,
)
from app.core.services.history import HistoryRepository  # noqa: E402
from app.core.services.thumbnails import ThumbnailStore  # noqa: E402
from app.core.services.ytdlp_manager import EXE_MAGIC, EXE_NAME  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402


FAKE_YTDLP_VERSION = "2026.08.19"
# Fake yt-dlp files start with this platform's executable signature ("MZ" on Windows, Mach-O on macOS).
FAKE_MAGIC = EXE_MAGIC[0]
FAKE_HEADER = FAKE_MAGIC + b"fake yt-dlp "


class FakeReleases:
    """Stands in for the official GitHub releases (no network in tests)."""

    def __init__(self, latest: str = FAKE_YTDLP_VERSION, payload: bytes | None = None) -> None:
        self.latest_version = latest
        self.payload = payload
        self.fail_check: Exception | None = None
        self.fail_download_after: int | None = None
        self.checksum: str | None = None
        self.downloads = 0

    def latest(self):
        from app.core.services.ytdlp_manager import GitHubReleases

        if self.fail_check:
            raise self.fail_check
        return GitHubReleases._build(self.latest_version, None)

    def release(self, version):
        from app.core.services.ytdlp_manager import GitHubReleases

        return GitHubReleases._build(version, None)

    def fetch_checksum(self, release) -> str:
        import hashlib

        return self.checksum or hashlib.sha256(self.body(release.version)).hexdigest()

    def body(self, version: str) -> bytes:
        if self.payload is not None:
            return self.payload
        return FAKE_MAGIC + f"fake yt-dlp {version} ".encode() * 400_000  # ~8 MB, plausible size

    def download(self, release, target: Path, progress, cancel) -> None:
        self.downloads += 1
        data = self.body(release.version)
        with target.open("wb") as handle:
            for start in range(0, len(data), 1024 * 1024):
                if cancel.is_set():
                    from app.core.services.ytdlp_manager import UpdateCancelled

                    raise UpdateCancelled()
                if self.fail_download_after is not None and start >= self.fail_download_after:
                    raise OSError("connection reset")
                handle.write(data[start:start + 1024 * 1024])
                progress(min(len(data), start + 1024 * 1024), len(data))


def fake_version_runner(broken: set[str] | None = None):
    """Runs "yt-dlp --version" on the fake files: prints the version written inside (FAKE_HEADER + b"X ...")."""
    import subprocess

    def run(args, timeout):
        exe = Path(args[0])
        data = exe.read_bytes()[:64] if exe.is_file() else b""
        if not data.startswith(FAKE_HEADER) or (broken and exe.name in broken):
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"boom")
        return subprocess.CompletedProcess(args, 0, stdout=data.split(b" ")[2] + b"\r\n", stderr=b"")

    return run


def make_fake_ytdlp(folder: Path, version: str | None = FAKE_YTDLP_VERSION, source: FakeReleases | None = None):
    from app.core.services.ytdlp_manager import YtDlpManager

    folder.mkdir(parents=True, exist_ok=True)
    source = source or FakeReleases(version or FAKE_YTDLP_VERSION)
    if version:
        (folder / EXE_NAME).write_bytes(FAKE_HEADER + version.encode() + b" ")
    return YtDlpManager(folder, (), source=source, runner=fake_version_runner())


class FakeProvider(DownloadProvider):
    """Deterministic engine: writes real bytes in chunks and honours the StopToken."""

    def __init__(self, chunks: int = 10, chunk_size: int = 1000, delay: float = 0.01) -> None:
        self.chunks = chunks
        self.chunk_size = chunk_size
        self.delay = delay
        self.failures: dict[str, list[ErrorKind]] = {}
        self.calls: dict[str, int] = {}
        self.running = 0
        self.max_running = 0
        self.resumed_from: dict[str, int] = {}
        self._lock = threading.Lock()
        self.gate = threading.Event()
        self.gate.set()
        import tempfile

        self.manager = make_fake_ytdlp(Path(tempfile.mkdtemp(prefix="luut-ytdlp-")))  # installed, up to date
        self.honor_resume = True      # False = source that cannot resume (starts over)
        self.finish_as_part = False   # True = broken engine that returns an incomplete file

    @property
    def engine(self) -> EngineInfo:
        return EngineInfo("fake", "1.0")

    @property
    def supports_resume(self) -> bool:
        return True

    def analyze(self, url: str) -> VideoInfo:
        if "invalid" in url:
            raise AppError(ErrorKind.UNSUPPORTED, "unsupported")
        return VideoInfo(url=url, title=f"Video {url.rsplit('/', 1)[-1]}", duration=10,
                         qualities=[QualityOption(0, 1000), QualityOption(720, 800)], containers=["mp4"])

    def download(self, job: DownloadJob, work_dir: Path, on_progress, on_stage, token: StopToken) -> Path:
        with self._lock:
            self.calls[job.url] = self.calls.get(job.url, 0) + 1
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            planned = self.failures.get(job.url)
            if planned:
                raise AppError(planned.pop(0), "planned failure")
            on_stage(Stage.DOWNLOADING)
            work_dir.mkdir(parents=True, exist_ok=True)
            part = work_dir / "media.mp4.part"
            if part.exists() and not self.honor_resume:
                part.unlink()
            existing = part.stat().st_size if part.exists() else 0
            if existing:
                self.resumed_from[job.url] = existing
            total = self.chunks * self.chunk_size
            with part.open("ab") as handle:
                written = existing
                while written < total:
                    self.gate.wait(5)
                    if token.is_set():
                        raise DownloadStopped(token.reason)
                    handle.write(b"x" * self.chunk_size)
                    handle.flush()
                    written += self.chunk_size
                    on_progress(DownloadProgress(written, total, 1000.0, 1))
                    time.sleep(self.delay)
            on_stage(Stage.PROCESSING)
            if self.finish_as_part:
                return part
            final = work_dir / "media.mp4"
            part.replace(final)
            return final
        finally:
            with self._lock:
                self.running -= 1


class FakeRegistry:
    """Stands in for the HKCU Run key: tests must never touch the real startup entry."""

    value: str | None = None

    def read(self) -> str | None:
        return FakeRegistry.value

    def write(self, command: str) -> None:
        FakeRegistry.value = command

    def delete(self) -> None:
        FakeRegistry.value = None


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    import app.system.autostart as autostart

    FakeRegistry.value = None
    monkeypatch.setattr(autostart, "RegistryBackend", FakeRegistry)
    monkeypatch.setattr(autostart, "LaunchAgentBackend", FakeRegistry)  # macOS: never write a real LaunchAgent
    return FakeRegistry


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "appdata"
    monkeypatch.setenv("LUUT_DATA_DIR", str(data))
    return data


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def settings_manager(db: Database, tmp_path: Path) -> SettingsManager:
    manager = SettingsManager(db)
    manager.update(default_dir=str(tmp_path / "Vídeos com espaço"), temp_dir=str(tmp_path / "temp"))
    return manager


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_manager(provider: FakeProvider, db: Database, settings_manager: SettingsManager, tmp_path: Path):
    created: list[DownloadManager] = []

    def factory(**kwargs) -> DownloadManager:
        manager = DownloadManager(kwargs.pop("provider", provider), HistoryRepository(db),
                                  lambda: settings_manager.settings, ThumbnailStore(tmp_path / "thumbs"),
                                  retry_delays=kwargs.pop("retry_delays", (0.01, 0.02)), **kwargs)
        created.append(manager)
        return manager

    yield factory
    for manager in created:
        manager.shutdown(timeout=2)


def make_request(settings_manager: SettingsManager, name: str, **overrides) -> DownloadRequest:
    output_dir = overrides.pop("output_dir", settings_manager.settings.default_dir)
    return DownloadRequest(url=f"https://example.com/{name}", output_dir=output_dir,
                           title=overrides.pop("title", f"Vídeo {name}"), **overrides)


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
