"""yt-dlp as an updatable dependency: versions, checks, install, validation, backup/rollback, blocking, UI."""

import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.errors import AppError, ErrorKind
from app.core.managers.settings_manager import SettingsManager
from app.core.models.download import DownloadRequest
from app.core.providers.base import DownloadJob
from app.core.providers.ytdlp_provider import YtDlpProvider, cli_format_args, parse_progress_line
from app.core.services.updates import UpdateBlocked, YtDlpUpdater
from app.core.services.ytdlp_manager import (
    EXE_NAME, NEW_NAME, GitHubReleases, UpdateCancelled, UpdateStatus, YtDlpError, YtDlpManager, _allowed,
    compare_versions, parse_version,
)
from tests.conftest import FAKE_HEADER, FakeReleases, fake_version_runner, make_fake_ytdlp, wait_until

OLD, NEW = "2026.08.19", "2026.09.22"


@pytest.fixture
def source():
    return FakeReleases(NEW)


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "bin"


def installed(folder, version):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / EXE_NAME).write_bytes(FAKE_HEADER + version.encode() + b" ")


def manager_for(folder, source, runner=None):
    return YtDlpManager(folder, (), source=source, runner=runner or fake_version_runner())


# ------------------------------------------------------------------ versions
def test_versions_are_compared_numerically():
    assert compare_versions("2026.9.1", "2026.10.1") == -1  # not a string comparison
    assert compare_versions("2026.09.20", "2026.9.20") == 0
    assert compare_versions("2026.09.20.123456", "2026.09.20") == 1  # nightly build suffix
    assert parse_version("v2026.08.19") == (2026, 8, 19)
    assert parse_version("latest") is None and parse_version("") is None
    with pytest.raises(ValueError):
        compare_versions("abc", "2026.01.01")


def test_only_official_hosts_are_accepted():
    assert _allowed("https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe")
    assert _allowed("https://objects.githubusercontent.com/github-production-release-asset/x")
    assert not _allowed("http://github.com/yt-dlp/yt-dlp")  # no plain http
    assert not _allowed("https://github.com.evil.example/yt-dlp.exe")
    assert not _allowed("https://example.com/yt-dlp.exe")
    release = GitHubReleases._build("2026.08.19", None)
    assert release.exe_url == f"https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/{EXE_NAME}"
    with pytest.raises(YtDlpError):
        GitHubReleases._build("../../evil", None)


# ------------------------------------------------------------------ TEST 1-4: status
def test_1_installed_and_up_to_date(folder):
    installed(folder, NEW)
    check = manager_for(folder, FakeReleases(NEW)).check_for_updates()
    assert check.status == UpdateStatus.UP_TO_DATE and check.installed == NEW


def test_2_old_version_reports_update(folder, source):
    installed(folder, OLD)
    check = manager_for(folder, source).check_for_updates()
    assert check.status == UpdateStatus.UPDATE_AVAILABLE
    assert (check.installed, check.latest) == (OLD, NEW)


def test_3_missing_yt_dlp(folder, source):
    manager = manager_for(folder, source)
    assert manager.get_executable_path() is None and manager.get_installed_version() is None
    assert manager.check_for_updates().status == UpdateStatus.NOT_INSTALLED
    with pytest.raises(AppError) as err:  # downloads refuse to start without it, with a friendly message
        YtDlpProvider(manager, ffmpeg_path="ffmpeg").analyze("https://example.com/v")
    assert err.value.kind == ErrorKind.ENGINE_MISSING
    assert manager.install() == NEW  # first installation
    assert manager.get_installed_version() == NEW


def test_4_offline_check_fails_softly(folder, source):
    installed(folder, OLD)
    source.fail_check = OSError("getaddrinfo failed")
    manager = manager_for(folder, source)
    check = manager.check_for_updates()
    assert check.status == UpdateStatus.CHECK_FAILED
    assert "conexão" in check.error
    assert manager.get_executable_path() is not None  # downloads keep working with the current version


def test_version_is_confirmed_by_running_the_executable(folder, source):
    installed(folder, OLD)
    manager = manager_for(folder, source)
    assert manager.get_installed_version() == OLD
    (folder / EXE_NAME).write_bytes(b"not an exe")
    os.utime(folder / EXE_NAME, (time.time() + 5, time.time() + 5))
    assert manager.get_installed_version() is None  # never trusts a stored value


# ------------------------------------------------------------------ TEST 5: busy
def test_5_update_blocked_during_downloads(folder, source, db):
    installed(folder, OLD)
    settings = SettingsManager(db)
    updater = YtDlpUpdater(manager_for(folder, source), settings, busy=lambda: True)
    with pytest.raises(UpdateBlocked) as err:
        updater.install()
    assert "downloads em andamento" in err.value.message
    assert source.downloads == 0 and updater.manager.get_installed_version() == OLD


def test_swap_waits_for_running_processes(folder, source):
    installed(folder, OLD)
    manager = manager_for(folder, source)
    lease = manager.lease()
    lease.__enter__()  # a yt-dlp process is running
    with pytest.raises(YtDlpError) as err:
        manager.install(wait_idle=0.2)
    assert "em uso" in err.value.message
    assert manager.get_installed_version(refresh=True) == OLD
    lease.__exit__(None, None, None)
    assert manager.install() == NEW


# ------------------------------------------------------------------ TEST 6-8: install safety
def test_6_interrupted_download_keeps_current_version(folder, source):
    installed(folder, OLD)
    source.fail_download_after = 2 * 1024 * 1024
    manager = manager_for(folder, source)
    with pytest.raises(YtDlpError) as err:
        manager.install()
    assert "mantida" in err.value.message
    assert manager.get_installed_version(refresh=True) == OLD
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]  # no temporary leftovers


def test_cancel_keeps_current_version(folder, source):
    installed(folder, OLD)
    manager = manager_for(folder, source)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UpdateCancelled):
        manager.install(cancel=cancel)
    assert manager.get_installed_version(refresh=True) == OLD
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]


@pytest.mark.parametrize("problem", ["checksum", "tiny", "not_exe", "wrong_version"])
def test_invalid_downloads_are_never_installed(folder, problem):
    installed(folder, OLD)
    source = FakeReleases(NEW)
    if problem == "checksum":
        source.checksum = "0" * 64
    elif problem == "tiny":
        source.payload = FAKE_HEADER + NEW.encode()
    elif problem == "not_exe":
        source.payload = b"<html>" + b"x" * 6_000_000
    else:
        source.payload = (FAKE_HEADER + b"2026.01.01 ") * 300_000  # answers with a different version
    manager = manager_for(folder, source)
    with pytest.raises(YtDlpError) as err:
        manager.install()
    assert "A atualização não pôde ser validada. A versão atual do yt-dlp foi mantida." == err.value.message
    assert manager.get_installed_version(refresh=True) == OLD
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]


def test_7_rollback_when_new_version_fails_after_replacement(folder, source):
    installed(folder, OLD)
    calls = {"n": 0}
    base = fake_version_runner()

    def runner(args, timeout):
        # the candidate validates fine, but once swapped in as the executable it no longer starts
        if Path(args[0]).name == EXE_NAME and NEW.encode() in Path(args[0]).read_bytes()[:64]:
            calls["n"] += 1
            return subprocess.CompletedProcess(args, 1, b"", b"crash")
        return base(args, timeout)

    manager = manager_for(folder, source, runner)
    with pytest.raises(YtDlpError) as err:
        manager.install()
    assert err.value.rolled_back and "versão anterior do yt-dlp foi restaurada" in err.value.message
    assert calls["n"] == 1
    assert manager.get_installed_version(refresh=True) == OLD
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]


def test_8_and_9_successful_update_persists(folder, source):
    installed(folder, OLD)
    manager = manager_for(folder, source)
    phases = []
    assert manager.install(progress=lambda phase, done, total: phases.append(phase)) == NEW
    assert {"download", "validate", "install"} <= set(phases)
    assert manager.get_installed_version() == NEW
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]  # backup removed after success
    assert manager_for(folder, FakeReleases(NEW)).get_installed_version() == NEW  # "reopened" app


def test_interrupted_swap_is_recovered_on_next_start(folder, source):
    folder.mkdir()
    (folder / f"{EXE_NAME}.backup").write_bytes(FAKE_HEADER + OLD.encode() + b" ")
    (folder / NEW_NAME).write_bytes(b"half")
    manager = manager_for(folder, source)
    manager.recover_interrupted_install()
    assert manager.get_installed_version() == OLD
    assert sorted(p.name for p in folder.iterdir()) == [EXE_NAME]


def test_seed_next_to_the_program_is_adopted(tmp_path, source):
    seed = tmp_path / "program" / "bin"
    installed(seed, OLD)
    managed = tmp_path / "profile" / "bin"
    manager = YtDlpManager(managed, (seed,), source=source, runner=fake_version_runner())
    assert manager.get_executable_path() == managed / EXE_NAME
    assert manager.install() == NEW
    assert manager.get_installed_version() == NEW
    assert (seed / EXE_NAME).read_bytes().startswith(FAKE_HEADER + OLD.encode())  # seed untouched


# ------------------------------------------------------------------ policy
def test_automatic_check_interval_and_persistence(folder, source, db):
    installed(folder, OLD)
    settings = SettingsManager(db)
    now = [datetime(2026, 9, 22, 23, 30)]
    updater = YtDlpUpdater(manager_for(folder, source), settings, busy=lambda: False, clock=lambda: now[0])
    assert updater.should_auto_check()
    result = updater.check()
    assert result.status == UpdateStatus.UPDATE_AVAILABLE
    assert settings.settings.ytdlp_last_check == "2026-09-22T23:30:00"
    assert settings.settings.ytdlp_latest_known == NEW and updater.state().update_available
    now[0] += timedelta(hours=5)
    assert not updater.should_auto_check()  # checked less than 24 h ago
    now[0] += timedelta(hours=20)
    assert updater.should_auto_check()
    settings.update(ytdlp_auto_check=False)
    assert not updater.should_auto_check()  # disabled: only the manual button checks
    source.fail_check = OSError("offline")
    before = settings.settings.ytdlp_last_check
    assert updater.check().status == UpdateStatus.CHECK_FAILED  # manual check always runs
    assert settings.settings.ytdlp_last_check == before  # failures are retried, not recorded as a check


def test_later_is_remembered(folder, source, db):
    installed(folder, OLD)
    updater = YtDlpUpdater(manager_for(folder, source), SettingsManager(db), busy=lambda: False)
    assert not updater.is_dismissed(NEW)
    updater.dismiss(NEW)
    assert updater.is_dismissed(NEW) and not updater.is_dismissed("2026.10.01")


# ------------------------------------------------------------------ provider CLI
def test_cli_arguments_match_the_format_choices():
    assert cli_format_args(DownloadJob("u", 720, "mp4"), True) == [
        "-f", "bv*[height<=?720]+ba/b[height<=?720]", "-S", "res,vcodec:h264,ext:mp4:m4a",
        "--merge-output-format", "mp4", "--remux-video", "mp4"]
    assert cli_format_args(DownloadJob("u", 0, "audio"), True) == [
        "-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "192K"]
    assert cli_format_args(DownloadJob("u", 0, "audio_m4a"), True)[-3:] == ["-x", "--audio-format", "m4a"]


def test_download_command_uses_the_manager_path(folder, source, tmp_path):
    installed(folder, NEW)
    provider = YtDlpProvider(manager_for(folder, source), ffmpeg_path="C:/ff/ffmpeg.exe")
    args = provider.download_args(folder / EXE_NAME, DownloadJob("https://example.com/v", 0, "mp4"), tmp_path)
    assert args[0] == str(folder / EXE_NAME)
    assert "--ignore-config" in args and args[-2:] == ["--", "https://example.com/v"]
    assert args[args.index("--ffmpeg-location") + 1] == "C:/ff/ffmpeg.exe"


def test_progress_line_parsing():
    data = parse_progress_line("LUUTPROG downloading|7168|223779|NA|3603160.48|2|C:\\w\\media.f395.mp4")
    assert data == {"status": "downloading", "downloaded_bytes": 7168, "total_bytes": 223779.0,
                    "total_bytes_estimate": None, "speed": 3603160.48, "eta": 2.0,
                    "filename": "C:\\w\\media.f395.mp4"}
    assert parse_progress_line("LUUTPROG broken") is None


# ------------------------------------------------------------------ UI (TEST 10: desktop + widget share it)
@pytest.fixture
def ui(qapp_module, db, tmp_path):
    from app.ui.context import AppContext
    from app.ui.main_window import MainWindow
    from tests.conftest import FakeProvider

    provider = FakeProvider()
    provider.manager = make_fake_ytdlp(tmp_path / "ytbin", OLD, FakeReleases(NEW))
    context = AppContext.create(db, provider)
    context.settings.update(default_dir=str(tmp_path / "V"), temp_dir=str(tmp_path / "t"))
    context.tutorial.set_dont_show(True)
    context.widget_tour_state.update(dont_show=True)
    window = MainWindow(context)
    window.show()
    yield window
    window.quit_app(cancel_downloads=True)
    window.deleteLater()
    qapp_module.processEvents()


@pytest.fixture(scope="module")
def qapp_module():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.ui import icons, theme

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(icons.stylesheet(theme.palette()))
    return app


def pump(app, condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_10_one_installation_for_desktop_and_widget(ui):
    ctx = ui._ctx
    assert ctx.ytdlp is ctx.provider.manager is ctx.update_manager.ytdlp.manager
    ctx.settings.update(widget_enabled=True)
    widget = ui.floating.widget
    assert widget._ctx.analyzer is ctx.analyzer  # the widget analyses/downloads through the same provider


def test_settings_panel_check_and_update(ui, qapp_module, monkeypatch):
    import app.ui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "ask", lambda *args: "later")
    controller = ui.ytdlp_updates
    ui.navigate("settings:updates")
    panel = ui.settings_page.updates_panel
    controller.start()
    assert pump(qapp_module, lambda: controller.installed == OLD and not controller.busy)
    assert panel.version_button.text() == OLD
    assert controller.check(manual=True)
    assert pump(qapp_module, lambda: controller.status == UpdateStatus.UPDATE_AVAILABLE and not controller.busy)
    assert panel.chip.text() == "↑ Atualização disponível"
    assert f"Nova: {NEW}" in panel.message.text() and not panel.install_button.isHidden()
    assert "Hoje às" in panel.last_check.text()
    assert controller.install()
    assert pump(qapp_module, lambda: controller.installed == NEW and not controller.busy, 20)
    assert panel.chip.text() == "✓ Atualizado" and panel.version_button.text() == NEW
    assert ui.about.ytdlp_version.text() == NEW


def test_ui_blocks_update_while_downloading(ui, qapp_module, monkeypatch):
    import app.ui.main_window as main_window_module

    asked = []
    monkeypatch.setattr(main_window_module, "ask", lambda *args: asked.append(args[1]) or "ok")
    provider = ui._ctx.provider
    provider.gate.clear()
    ui._ctx.downloads.add(DownloadRequest(url="https://example.com/long", output_dir=ui._ctx.settings.settings.default_dir))
    assert pump(qapp_module, lambda: provider.running == 1)
    assert not ui.ytdlp_updates.install(manual=True)
    assert pump(qapp_module, lambda: asked == ["Downloads em andamento"])
    assert ui._ctx.ytdlp.get_installed_version() == OLD
    provider.gate.set()


def test_pending_update_installs_when_queue_is_idle(ui, qapp_module, monkeypatch):
    import app.ui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "ask", lambda *args: "ok")
    ctx = ui._ctx
    ctx.settings.update(ytdlp_update_when_idle=True)
    provider = ctx.provider
    provider.gate.clear()
    task = ctx.downloads.add(DownloadRequest(url="https://example.com/wait", output_dir=ctx.settings.settings.default_dir))
    assert pump(qapp_module, lambda: provider.running == 1)
    ui.ytdlp_updates.install(manual=True)
    assert ctx.settings.settings.ytdlp_pending_install
    provider.gate.set()
    assert wait_until(lambda: ctx.downloads.get(task.id).status.is_terminal)
    assert pump(qapp_module, lambda: ctx.ytdlp.get_installed_version() == NEW, 20)
    assert pump(qapp_module, lambda: not ctx.settings.settings.ytdlp_pending_install)


def test_first_run_without_yt_dlp_offers_install(ui, qapp_module, monkeypatch, tmp_path):
    import app.ui.main_window as main_window_module

    for leftover in (tmp_path / "ytbin").iterdir():
        leftover.unlink()
    asked = []
    monkeypatch.setattr(main_window_module, "ask", lambda *args: asked.append(args[1]) or "later")
    ui.ytdlp_updates.start()
    assert pump(qapp_module, lambda: asked == ["yt-dlp não encontrado"])
    assert pump(qapp_module, lambda: ui.home.engine_banner.isVisibleTo(ui.home))
    ui.navigate("settings:updates")
    panel = ui.settings_page.updates_panel
    assert panel.chip.text() == "! yt-dlp não instalado" and panel.install_button.text() == "Instalar yt-dlp"
    ui.ytdlp_updates.install()
    assert pump(qapp_module, lambda: ui.ytdlp_updates.installed == NEW and not ui.ytdlp_updates.busy, 20)
    assert not ui.home.engine_banner.isVisibleTo(ui.home)


def test_automatic_check_is_discreet(ui, qapp_module, monkeypatch):
    import app.ui.main_window as main_window_module

    asked, toasts = [], []
    monkeypatch.setattr(main_window_module, "ask", lambda *args: asked.append(args[1]) or "later")
    monkeypatch.setattr(ui, "notify", lambda *args, **kw: toasts.append(args[0]))
    ui._ctx.settings.update(ytdlp_last_check="")
    ui.ytdlp_updates.start()
    assert pump(qapp_module, lambda: toasts == ["↑ Atualização disponível"])
    assert asked == []  # no modal dialog for an automatic check


def test_diagnostics_lists_yt_dlp(ui):
    from app.core.services.diagnostics import ytdlp_items

    items = {i.label: i.value for i in ytdlp_items(ui._ctx.settings.settings, ui._ctx.ytdlp)}
    assert items["yt-dlp"] == OLD and items["yt-dlp encontrado"] == "SIM"
    assert items["Caminho do yt-dlp"].endswith(EXE_NAME)
