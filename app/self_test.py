"""Headless end-to-end check of the packaged engine:

    "Luut Video Downloader.exe" --self-test <url> <output folder>

Analyses the URL, downloads it (best quality, MP4) and writes `self-test.json` to the output folder.
Exit code 0 means the whole pipeline (engine, ffmpeg, filesystem) works on this machine.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from app import APP_VERSION
from app.core.managers.download_manager import DownloadManager
from app.core.models.download import DownloadRequest, DownloadStatus
from app.core.models.settings import AppSettings
from app.core.models.video import CONTAINER_MP4
from app.core.providers.ytdlp_provider import YtDlpProvider
from app.core.services.analyzer import Analyzer
from app.core.services.history import HistoryRepository
from app.core.services.ytdlp_manager import YtDlpError, default_manager
from app.infrastructure.database import Database
from app.infrastructure.tools import ffmpeg_version, find_ffmpeg
from app.utils.formatters import now_iso


def run_self_test(url: str, output_dir: str, timeout: float = 600) -> int:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"version": APP_VERSION, "started_at": now_iso(), "url": url,
                                 "ffmpeg": find_ffmpeg(), "ffmpeg_version": ffmpeg_version()}
    ytdlp = default_manager()
    ytdlp.recover_interrupted_install()
    provider = YtDlpProvider(ytdlp)
    try:
        if not ytdlp.is_installed():  # clean install: get yt-dlp the same way the app does
            report["ytdlp_installed_now"] = ytdlp.install()
        report["ytdlp"] = {"version": ytdlp.get_installed_version(), "path": str(ytdlp.get_executable_path())}
        info = Analyzer(provider).analyze(url).info
        report["analysis"] = {"title": info.title, "duration": info.duration, "max_height": info.max_height,
                              "qualities": [q.label for q in info.qualities], "containers": info.containers}
        settings = AppSettings(default_dir=str(out), temp_dir=str(out / ".temp"))
        manager = DownloadManager(provider, HistoryRepository(Database(":memory:")), lambda: settings)
        container = CONTAINER_MP4 if CONTAINER_MP4 in info.containers else info.containers[0]
        task = manager.add(DownloadRequest(url=info.url, output_dir=str(out), title=info.title, container=container))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not manager.get(task.id).status.is_terminal:
            time.sleep(0.2)
        result = manager.get(task.id)
        manager.shutdown(cancel=True)
        shutil.rmtree(settings.temp_dir, ignore_errors=True)
        report["download"] = {"status": result.status.value, "file": result.file_path, "size": result.file_size,
                              "error": result.error_message}
        ok = result.status == DownloadStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        report["error"] = f"{type(exc).__name__}: {getattr(exc, 'user_message', exc)}"
        ok = False
    report["ok"] = ok
    report["finished_at"] = now_iso()
    (out / "self-test.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1


def _app_is_open() -> bool:
    from PySide6.QtCore import QCoreApplication

    from app.system.single_instance import instance_running, server_name
    from app.utils.paths import is_fresh_profile

    _app = QCoreApplication.instance() or QCoreApplication([])
    return instance_running(server_name("-fresh" if is_fresh_profile() else ""))


def run_ytdlp_command(command: str, report_path: str, version: str | None = None) -> int:
    """Support/diagnostics without the interface (same YtDlpManager as the app):

        "Luut Video Downloader.exe" --ytdlp-status <report.json>
        "Luut Video Downloader.exe" --ytdlp-update <report.json> [version]
    """
    manager = default_manager()
    manager.recover_interrupted_install()
    report: dict[str, object] = {"command": command, "started_at": now_iso(), "path": str(manager.managed_path),
                                 "installed_before": manager.get_installed_version()}
    ok = True
    try:
        if command == "--ytdlp-update" and _app_is_open():
            raise YtDlpError("Feche o Luut Video Downloader antes de atualizar o yt-dlp por linha de comando "
                             "(ou use Configurações → Atualizações).", "Application is running")
        if command == "--ytdlp-update":
            release = manager.source.release(version) if version else None
            report["installed_now"] = manager.install(release)
        check = manager.check_for_updates()
        report.update(status=check.status.value, latest=check.latest, error=check.error)
    except YtDlpError as error:
        report.update(error=error.message, detail=error.detail, rolled_back=error.rolled_back)
        ok = False
    report["installed_after"] = manager.get_installed_version(refresh=True)
    report["found"] = manager.get_executable_path() is not None
    report["finished_at"] = now_iso()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1
