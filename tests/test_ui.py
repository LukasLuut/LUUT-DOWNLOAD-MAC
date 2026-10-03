"""UI flow tests (offscreen Qt) using the fake engine."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.models.download import DownloadStatus  # noqa: E402
from app.ui import icons, theme  # noqa: E402
from app.ui.context import AppContext  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import FakeProvider  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(icons.stylesheet(theme.palette()))
    return app


def pump(app, condition, timeout=8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


@pytest.fixture
def window(qapp, db, tmp_path):
    context = AppContext.create(db, FakeProvider())
    context.settings.update(default_dir=str(tmp_path / "Meus Vídeos"), temp_dir=str(tmp_path / "tmp"),
                            notify_completed=True)
    context.tutorial.set_dont_show(True)
    win = MainWindow(context)
    win.show()
    yield win
    win.quit_app(cancel_downloads=True)
    win.deleteLater()
    qapp.processEvents()


def test_navigation_between_all_pages(qapp, window):
    for key in ("home", "downloads", "history", "settings", "about"):
        window.navigate(key)
        qapp.processEvents()
        assert window.stack.currentWidget() is window.pages[key]
        assert window.sidebar._buttons[key].isChecked()


def test_main_flow_analyze_queue_complete_history(qapp, window):
    home = window.home
    home.set_url("https://example.com/clip", analyze=True)
    assert pump(qapp, home.has_result)
    assert home.quality.itemText(0).startswith("Melhor disponível")
    assert home.quality.count() == 2
    assert home.container.itemData(0) == "mp4"
    assert home.destination.text().endswith("Meus Vídeos")
    home.quality.setCurrentIndex(1)
    home.add_to_queue()
    manager = window._ctx.downloads
    assert pump(qapp, lambda: any(t.status == DownloadStatus.COMPLETED for t in manager.tasks()))
    task = manager.tasks()[0]
    assert task.quality_height == 720
    assert Path(task.file_path).exists()
    window.navigate("history")
    assert pump(qapp, lambda: window.history._shown == 1)
    card = window.downloads._cards[task.id]
    assert card.chip.text() == "✓ Concluído"


def test_invalid_url_shows_friendly_error(qapp, window):
    home = window.home
    home.set_url("isso não é um link", analyze=True)
    assert home.error_label.text() == "O link informado não parece ser válido."
    home.set_url("https://example.com/invalid", analyze=True)
    assert pump(qapp, lambda: home.error_label.text() == "Não foi possível analisar este link.")
    assert not home.has_result()


def test_empty_url(qapp, window):
    window.home.set_url("", analyze=True)
    assert window.home.error_label.text() == "Cole um link para analisar."


def test_paste_shortcut_and_ctrl_l(qapp, window):
    QGuiApplication.clipboard().setText("https://example.com/pasted")
    window.navigate("about")
    window.setFocus()
    window.home.paste_from_clipboard()
    assert window.home.url_input.text() == "https://example.com/pasted"
    assert pump(qapp, window.home.has_result)


def test_drop_url(qapp, window):
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/dropped")])
    event = QDropEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    assert window.home.url_input.text() == "https://example.com/dropped"
    assert pump(qapp, window.home.has_result)


def test_theme_switch_and_settings_persist(qapp, window):
    window._ctx.settings.update(theme="light")
    assert theme.palette().name == "light"
    window._ctx.settings.update(theme="dark")
    assert theme.palette().name == "dark"


def test_downloads_page_delete_key_removes_selected(qapp, window):
    provider = window._ctx.provider
    provider.gate.clear()
    manager = window._ctx.downloads
    from app.core.models.download import DownloadRequest
    running = manager.add(DownloadRequest(url="https://example.com/a", output_dir=window._ctx.settings.settings.default_dir))
    waiting = manager.add(DownloadRequest(url="https://example.com/b", output_dir=window._ctx.settings.settings.default_dir))
    window.navigate("downloads")
    assert pump(qapp, lambda: waiting.id in window.downloads._cards)
    window.downloads.select(waiting.id)
    assert window.downloads.remove_selected()
    assert manager.get(waiting.id) is None
    window.downloads.select(running.id)
    window.downloads.remove_selected()  # active: refused with a warning
    assert manager.get(running.id) is not None
    provider.gate.set()


@pytest.mark.parametrize("choice,expect_quit", [("back", False), ("background", False), ("cancel", True),
                                                ("pause", True)])
def test_close_with_active_downloads(qapp, window, monkeypatch, choice, expect_quit):
    from PySide6.QtGui import QCloseEvent

    from app.core.models.download import DownloadRequest
    import app.ui.main_window as main_window_module

    provider = window._ctx.provider
    provider.gate.clear()
    manager = window._ctx.downloads
    task = manager.add(DownloadRequest(url="https://example.com/long", output_dir=window._ctx.settings.settings.default_dir))
    assert pump(qapp, lambda: provider.running == 1)
    asked = []
    monkeypatch.setattr(main_window_module, "ask", lambda *args: asked.append(args[1]) or choice)
    event = QCloseEvent()
    window.closeEvent(event)
    assert asked == ["Existem downloads em andamento."]
    assert event.isAccepted() == expect_quit
    if choice == "pause":
        assert manager.get(task.id).status == DownloadStatus.PAUSED
    elif expect_quit:
        assert manager.get(task.id).status == DownloadStatus.CANCELLED
    else:
        assert manager.get(task.id).status == DownloadStatus.DOWNLOADING
    provider.gate.set()


def test_close_to_tray_when_enabled(qapp, window):
    from PySide6.QtGui import QCloseEvent

    window._ctx.settings.update(minimize_to_tray=True)
    event = QCloseEvent()
    window.closeEvent(event)
    if window.tray.available:
        assert not event.isAccepted()
    window._ctx.settings.update(minimize_to_tray=False)


# ------------------------------------------------------------------ first experience / tutorial
@pytest.fixture
def new_user_window(qapp, db, tmp_path):
    context = AppContext.create(db, FakeProvider())
    context.settings.update(default_dir=str(tmp_path / "Vídeos"), temp_dir=str(tmp_path / "tmp"))
    win = MainWindow(context)
    win.resize(1366, 728)
    win.show()
    yield win
    win.quit_app(cancel_downloads=True)
    win.deleteLater()
    qapp.processEvents()


def run_tutorial(qapp, win, action="next"):
    steps = win._ctx.tutorial.steps
    seen = []
    for _ in range(len(steps)):
        pump(qapp, lambda: win.tutorial.overlay is not None and win.tutorial.overlay.isVisible(), 3)
        overlay = win.tutorial.overlay
        seen.append(win._ctx.tutorial.current.id)
        assert overlay.rect().contains(overlay.bubble.geometry())
        getattr(win.tutorial, action)()
        pump(qapp, lambda: True, 0.1)
    return seen


def test_first_launch_runs_full_tutorial_without_touching_data(qapp, new_user_window):
    win = new_user_window
    ctx = win._ctx
    win.start_up()
    assert win.tutorial.running
    seen = run_tutorial(qapp, win)
    assert seen == [s.id for s in ctx.tutorial.steps]
    assert not win.tutorial.running
    assert ctx.tutorial.state.completed and not ctx.tutorial.should_autostart()
    # Demo data is gone and nothing real was created.
    assert win.downloads.demo_card is None and not win.home.demo_chip.isVisible()
    assert not win.home.has_result()
    assert ctx.downloads.tasks() == [] and ctx.history.count() == 0
    assert win.home.stat_total.value.text() == "0" and win.home.hello_card.isVisible()


def test_demo_mode_never_adds_downloads(qapp, new_user_window):
    win = new_user_window
    win.start_tutorial()
    for _ in range(7):  # up to the "Adicionar à fila" step
        win.tutorial.next()
        pump(qapp, lambda: True, 0.1)
    assert win._ctx.tutorial.current.id == "queue"
    win.home.add_to_queue()
    win._add_shortcut()
    assert win._ctx.downloads.tasks() == []
    win.tutorial.skip()


def test_skip_then_dont_show_again(qapp, new_user_window):
    win = new_user_window
    ctx = win._ctx
    win.start_tutorial()
    pump(qapp, lambda: win.tutorial.overlay is not None, 2)
    win.tutorial.skip()
    assert ctx.tutorial.should_autostart()  # skipped: offered again next time
    win.start_tutorial()
    pump(qapp, lambda: win.tutorial.overlay is not None, 2)
    win.tutorial.overlay.bubble.dont_show.setChecked(True)
    win.tutorial.skip()
    assert not ctx.tutorial.should_autostart()
    win.navigate("settings")
    assert not win.settings_page.tutorial_toggle.isChecked()
    win.settings_page.tutorial_toggle.setChecked(True)  # "Mostrar tutorial na próxima abertura"
    assert ctx.tutorial.should_autostart()


def test_settings_button_restarts_tutorial(qapp, new_user_window):
    win = new_user_window
    win._ctx.tutorial.set_dont_show(True)
    win.start_up()
    assert not win.tutorial.running
    win.settings_page.start_tutorial.emit()
    assert win.tutorial.running
    win.tutorial.skip()


def test_empty_states_for_new_user(qapp, new_user_window):
    win = new_user_window
    win.navigate("history")
    assert win.history.empty.isVisible()
    assert win.history.empty_title.text() == "Seu histórico está vazio."
    win.navigate("downloads")
    assert win.downloads.empty.isVisible()
    win.navigate("home")
    assert win.home.hello_card.isVisible() and not win.home.activity_row.isVisible()


@pytest.mark.parametrize("choice", ["resume", "review", "discard"])
def test_recovery_prompt(qapp, db, tmp_path, monkeypatch, choice):
    import app.ui.main_window as main_window_module
    from app.core.models.download import DownloadRequest

    context = AppContext.create(db, FakeProvider())
    context.settings.update(default_dir=str(tmp_path / "V"), temp_dir=str(tmp_path / "t"))
    context.tutorial.set_dont_show(True)
    context.provider.gate.clear()
    first = context.downloads.add(DownloadRequest(url="https://example.com/r1", output_dir=str(tmp_path / "V")))
    second = context.downloads.add(DownloadRequest(url="https://example.com/r2", output_dir=str(tmp_path / "V")))
    assert pump(qapp, lambda: context.provider.running == 1)
    context.downloads.shutdown()  # "crash"/close while downloading
    context.provider.gate.set()

    reopened = AppContext.create(db, FakeProvider())
    reopened.downloads.restore()
    answers = [choice, "discard"]
    monkeypatch.setattr(main_window_module, "ask", lambda *args: answers.pop(0))
    win = MainWindow(reopened)
    win.show()
    win.start_up()
    manager = reopened.downloads
    if choice == "resume":
        assert pump(qapp, lambda: all(manager.get(t.id).status == DownloadStatus.COMPLETED for t in (first, second)))
    elif choice == "review":
        assert manager.is_held and win.stack.currentWidget() is win.downloads
        pump(qapp, lambda: True, 0.2)
        assert win.downloads.held_banner.isVisible()
    else:
        assert all(manager.get(t.id).status == DownloadStatus.CANCELLED for t in (first, second))
    win.quit_app(cancel_downloads=True)
    win.deleteLater()


def test_reset_asks_and_restarts_with_reset_flag(qapp, new_user_window, monkeypatch):
    import app.ui.main_window as main_window_module

    calls = []
    monkeypatch.setattr(main_window_module, "restart_application", lambda args: calls.append(args))
    monkeypatch.setattr(main_window_module, "ask", lambda *args: "cancel")
    new_user_window.request_reset()
    assert calls == [] and not new_user_window._quitting
    monkeypatch.setattr(main_window_module, "ask", lambda *args: "erase")
    new_user_window.request_reset()
    assert calls == [["--reset-data"]] and new_user_window._quitting


def test_delete_record_and_file_uses_recycle_bin(qapp, window, monkeypatch, tmp_path):
    import app.ui.task_actions as actions_module
    from app.core.models.download import DownloadTask

    video = tmp_path / "vídeo.mp4"
    video.write_bytes(b"v")
    task = DownloadTask(url="https://x.com", output_dir=str(tmp_path), status=DownloadStatus.COMPLETED,
                        file_path=str(video))
    window._ctx.history.save(task)
    trashed = []
    monkeypatch.setattr(actions_module.QFile, "moveToTrash", lambda path: trashed.append(path) or True)
    monkeypatch.setattr(actions_module, "ask", lambda *args: "delete")
    actions_module.TaskActions(window._ctx, window).run("delete", task.id)  # record only
    assert window._ctx.history.get(task.id) is None and video.exists() and trashed == []
    window._ctx.history.save(task)
    actions_module.TaskActions(window._ctx, window).run("delete_with_file", task.id)
    assert window._ctx.history.get(task.id) is None and trashed == [str(video)]
