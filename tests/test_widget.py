"""Floating widget, global hotkeys, clipboard detection, Atalhos tab, tray, single instance and startup (offscreen)."""

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.models.download import DownloadRequest, DownloadStatus  # noqa: E402
from app.core.models.settings import AppSettings  # noqa: E402
from app.system.global_hotkeys import HotkeyBackend, virtual_key  # noqa: E402
from app.ui import icons, theme  # noqa: E402
from app.ui.context import AppContext  # noqa: E402
from app.ui.floating_widget import (  # noqa: E402
    FloatingWidget,
    clamp_to_screens,
    corner_position,
    is_reachable,
    resolve_position,
)
from app.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import FakeProvider  # noqa: E402


class FakeHotkeys(HotkeyBackend):
    """Records what would be registered with Windows and lets the test "press" a hotkey."""

    def __init__(self) -> None:
        self.registered: dict[int, tuple[int, int]] = {}
        self.refuse: set[int] = set()

    def register(self, hotkey_id, modifiers, vk):
        if vk in self.refuse:
            return False
        self.registered[hotkey_id] = (modifiers, vk)
        return True

    def unregister(self, hotkey_id):
        self.registered.pop(hotkey_id, None)

    def press(self, combo: str) -> None:
        from app.core.shortcuts import parse_combo
        from app.system.global_hotkeys import modifier_flags

        parsed = parse_combo(combo)
        wanted = (modifier_flags(parsed), virtual_key(parsed.key))
        for hotkey_id, registration in list(self.registered.items()):
            if registration == wanted and self.callback:
                self.callback(hotkey_id)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(icons.stylesheet(theme.palette()))
    return app


def dispose(app, window) -> None:
    window.quit_app(cancel_downloads=True)
    window.deleteLater()
    app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # really gone before the db closes
    app.processEvents()


def pump(app, condition=lambda: False, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


@pytest.fixture
def hotkeys():
    return FakeHotkeys()


@pytest.fixture
def win(qapp, db, tmp_path, hotkeys):
    context = AppContext.create(db, FakeProvider())
    context.settings.update(default_dir=str(tmp_path / "Vídeos"), temp_dir=str(tmp_path / "tmp"))
    context.tutorial.set_dont_show(True)
    context.widget_tour_state.update(dont_show=True)
    window = MainWindow(context, hotkey_backend=hotkeys)
    window.show()
    yield window
    dispose(qapp, window)


def enable_widget(qapp, window) -> FloatingWidget:
    window._ctx.settings.update(widget_enabled=True)
    assert pump(qapp, lambda: window.floating.widget is not None and window.floating.widget.isVisible())
    return window.floating.widget


# ------------------------------------------------------------------ enable/disable
def test_widget_disabled_by_default_uses_no_resources(qapp, win, hotkeys):
    assert AppSettings().widget_enabled is False
    controller = win.floating
    assert controller.widget is None and controller.bubble is None
    assert not controller.clipboard.enabled
    assert "widget.toggle" not in win.hotkeys.registered_actions()   # widget hotkeys inactive
    assert "app.toggle" in win.hotkeys.registered_actions()
    assert not controller.show()
    assert not win.tray.show_widget_action.isVisible()


def test_enable_shows_widget_and_registers_its_hotkey(qapp, win):
    widget = enable_widget(qapp, win)
    assert "widget.toggle" in win.hotkeys.registered_actions()
    assert win.tray.show_widget_action.isVisible()
    win._ctx.settings.update(widget_enabled=False)
    pump(qapp, timeout=0.2)
    assert win.floating.widget is None
    assert "widget.toggle" not in win.hotkeys.registered_actions()
    assert not widget.isVisible()


def test_global_hotkey_toggles_without_creating_windows(qapp, win, hotkeys):
    widget = enable_widget(qapp, win)
    states = []
    for _ in range(4):
        hotkeys.press("Ctrl+Shift+L")
        pump(qapp, timeout=0.05)
        states.append(win.floating.is_visible)
    assert states == [False, True, False, True]
    assert win.floating.widget is widget
    assert len([w for w in QApplication.topLevelWidgets() if isinstance(w, FloatingWidget) and w.isVisible()]) == 1
    assert win.isVisible()  # the main window's own hotkey (Ctrl+Alt+L) was not triggered


def test_disabled_global_shortcut_is_released(qapp, win, hotkeys):
    enable_widget(qapp, win)
    win._ctx.shortcuts.disable("widget.toggle")
    assert "widget.toggle" not in win.hotkeys.registered_actions()
    hotkeys.press("Ctrl+Shift+L")
    assert win.floating.is_visible  # nothing happened
    win._ctx.shortcuts.assign("widget.toggle", "Ctrl+Alt+W")
    assert win.hotkeys.registered_actions()["widget.toggle"] == "Ctrl+Alt+W"


def test_hotkey_refused_by_windows_is_reported(qapp, db, tmp_path, hotkeys):
    hotkeys.refuse.add(virtual_key("L"))
    context = AppContext.create(db, FakeProvider())
    context.settings.update(temp_dir=str(tmp_path / "t"), widget_enabled=True)
    context.tutorial.set_dont_show(True)
    window = MainWindow(context, hotkey_backend=hotkeys)
    assert context.shortcuts.global_status("widget.toggle") is False
    window.navigate("settings:shortcuts")
    row = window.settings_page.shortcuts_panel.rows["widget.toggle"]
    row.refresh()
    assert not row.unavailable.isHidden()
    dispose(qapp, window)


# ------------------------------------------------------------------ compact / position / window options
def test_minimize_to_compact_button_and_expand(qapp, win):
    widget = enable_widget(qapp, win)
    win.floating.minimize()
    bubble = win.floating.bubble
    assert bubble.isVisible() and not widget.isVisible()
    assert win._ctx.settings.settings.widget_compact
    bubble.clicked_without_drag()
    assert widget.isVisible() and not bubble.isVisible()
    assert not win._ctx.settings.settings.widget_compact


def test_compact_count_shows_pending_downloads(qapp, win):
    enable_widget(qapp, win)
    provider = win._ctx.provider
    provider.gate.clear()
    for name in ("a", "b", "c"):
        win._ctx.downloads.add(DownloadRequest(url=f"https://example.com/{name}", output_dir=str(Path.home())))
    win.floating.minimize()
    assert pump(qapp, lambda: win.floating.bubble.count.text() == "3")
    provider.gate.set()


def test_drag_saves_position(qapp, win):
    widget = enable_widget(qapp, win)
    widget.move(QPoint(120, 140))
    widget.moved_by_user()
    settings = win._ctx.settings.settings
    assert settings.widget_has_position and (settings.widget_x, settings.widget_y) == (120, 140)


def test_position_helpers_multi_monitor_and_offscreen_recovery():
    left = QRect(-1920, 0, 1920, 1040)
    primary = QRect(0, 0, 2560, 1400)
    size = QSize(420, 300)
    settings = AppSettings(widget_position="last", widget_has_position=True, widget_x=-1500, widget_y=200)
    assert resolve_position(settings, size, [left, primary], primary) == QPoint(-1500, 200)  # second monitor
    lost = AppSettings(widget_position="last", widget_has_position=True, widget_x=5000, widget_y=5000)
    point = resolve_position(lost, size, [primary], primary)  # monitor disconnected -> safe corner
    assert primary.contains(QRect(point, size))
    for corner in ("bottom_right", "bottom_left", "top_right", "top_left"):
        assert primary.contains(QRect(corner_position(corner, size, primary), size))
    assert not is_reachable(QRect(QPoint(100, -290), size), [primary])  # header above the screen
    clamped = clamp_to_screens(QRect(QPoint(2400, 1300), size), [left, primary])
    assert primary.contains(QRect(clamped, size))


def test_opacity_and_always_on_top(qapp, win):
    widget = enable_widget(qapp, win)
    win._ctx.settings.update(widget_opacity=80, widget_always_on_top=True)
    assert abs(widget.windowOpacity() - 0.8) < 0.02
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    win._ctx.settings.update(widget_opacity=10)  # clamped to the allowed range
    assert win._ctx.settings.settings.widget_opacity == 70
    win._ctx.settings.update(widget_always_on_top=False)
    assert not widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


# ------------------------------------------------------------------ download flow through the shared core
def test_paste_analyze_and_start_goes_to_the_shared_queue(qapp, win):
    widget = enable_widget(qapp, win)
    QGuiApplication.clipboard().setText("https://example.com/widgetclip")
    widget.paste()
    assert widget.url_input.text() == "https://example.com/widgetclip"
    assert win._ctx.downloads.tasks() == []  # pasting never downloads
    widget._shortcut_analyze()
    assert pump(qapp, widget.analysis_ready)
    assert widget.quality.count() == 2 and widget.quality.itemText(0).startswith("Melhor disponível")
    assert widget.container.itemText(0) == "MP4"
    assert widget.destination.full_text().endswith("Vídeos")
    widget.quality.setCurrentIndex(1)
    assert widget._shortcut_analyze() is False  # same combo now means "Iniciar download"
    assert widget._shortcut_start() is True
    tasks = win._ctx.downloads.tasks()
    assert len(tasks) == 1 and tasks[0].quality_height == 720
    # Back to "new download" at once: empty URL field with focus, ready for the next link.
    assert widget.mode == "form" and widget.tracked_task_id == tasks[0].id
    assert widget.url_input.text() == "" and not widget.result.isVisibleTo(widget)
    assert "Adicionado à fila" in widget.status_text.text()
    assert pump(qapp, lambda: tasks[0].id in win.downloads._cards)  # the desktop shows the same task
    assert pump(qapp, lambda: win._ctx.downloads.get(tasks[0].id).status == DownloadStatus.COMPLETED)
    widget.url_input.setText("https://example.com/next")
    assert not widget.status_row.isVisibleTo(widget)
    widget.confirm()
    assert pump(qapp, widget.analysis_ready)
    widget.confirm()
    assert len(win._ctx.downloads.tasks()) == 2  # queue grows without leaving the form


def test_desktop_downloads_appear_in_widget_queue(qapp, win):
    widget = enable_widget(qapp, win)
    widget.set_queue_open(True)
    win._ctx.provider.gate.clear()
    task = win._ctx.downloads.add(DownloadRequest(url="https://example.com/fromdesktop",
                                                  output_dir=win._ctx.settings.settings.default_dir, title="Desk"))
    assert pump(qapp, lambda: widget.row(task.id) is not None)
    assert widget.queue_button.text() == "Fila 1"
    win._ctx.provider.gate.set()


def test_widget_pause_resume_cancel(qapp, win):
    widget = enable_widget(qapp, win)
    provider = win._ctx.provider
    provider.gate.clear()
    manager = win._ctx.downloads
    task = manager.add(DownloadRequest(url="https://example.com/ctl", output_dir=win._ctx.settings.settings.default_dir))
    assert pump(qapp, lambda: provider.running == 1)
    widget.track(task.id)
    assert widget._act_on_current("pause")
    provider.gate.set()
    assert pump(qapp, lambda: manager.get(task.id).status == DownloadStatus.PAUSED)
    assert pump(qapp, lambda: widget._p_buttons["resume"].isVisibleTo(widget))
    provider.gate.clear()
    widget._progress_action("resume")
    assert pump(qapp, lambda: provider.running == 1)
    widget._progress_action("cancel")
    provider.gate.set()
    assert pump(qapp, lambda: manager.get(task.id).status == DownloadStatus.CANCELLED)


def test_queue_rows_have_contextual_actions(qapp, win, monkeypatch, tmp_path):
    import app.infrastructure.filesystem as fs

    widget = enable_widget(qapp, win)
    manager = win._ctx.downloads
    task = manager.add(DownloadRequest(url="https://example.com/done",
                                       output_dir=win._ctx.settings.settings.default_dir, title="Pronto"))
    assert pump(qapp, lambda: manager.get(task.id).status == DownloadStatus.COMPLETED)
    widget.set_queue_open(True)
    assert pump(qapp, lambda: widget.row(task.id) is not None)
    row = widget.row(task.id)
    visible = {a for a, b in row._buttons.items() if b.isVisibleTo(row)}
    assert visible == {"open_file", "open_folder", "copy_path", "redownload", "forget"}
    opened = []
    monkeypatch.setattr(fs, "open_file", lambda path: opened.append(path) or True)
    row._buttons["open_file"].click()
    assert opened and Path(opened[0]).name.startswith("Pronto")
    assert widget.move_selection(1) and widget.selected_task_id == task.id
    row._buttons["forget"].click()
    assert manager.get(task.id) is None and win._ctx.history.get(task.id) is None
    assert Path(opened[0]).exists()  # the file itself is kept


def test_huge_queue_is_accepted_and_rendered_compactly(qapp, win):
    widget = enable_widget(qapp, win)
    win._ctx.provider.gate.clear()
    for index in range(150):
        win._ctx.downloads.add(DownloadRequest(url=f"https://example.com/many{index}",
                                               output_dir=win._ctx.settings.settings.default_dir))
    assert len(win._ctx.downloads.tasks()) == 150  # no artificial limit on the queue
    widget.set_queue_open(True)
    assert pump(qapp, lambda: widget.queue_more.isVisibleTo(widget))
    assert "+90" in widget.queue_more.text()
    assert win._ctx.downloads.active_count() == 1  # concurrency stays at the configured 1
    win._ctx.provider.gate.set()


# ------------------------------------------------------------------ clipboard
def test_clipboard_detection_offers_but_never_downloads(qapp, win):
    widget = enable_widget(qapp, win)
    monitor = win.floating.clipboard
    assert not monitor.enabled
    win._ctx.settings.update(widget_detect_clipboard=True)
    assert monitor.enabled
    monitor._clipboard.ownsClipboard = lambda: False  # offscreen: pretend another program copied it
    QGuiApplication.clipboard().setText("https://example.com/copied")
    monitor._on_changed()
    assert widget.banner.isVisibleTo(widget) and widget.banner_url.full_text() == "https://example.com/copied"
    assert win._ctx.downloads.tasks() == []
    widget.dismiss_banner()
    monitor._on_changed()  # same URL is not offered again
    assert not widget.banner.isVisibleTo(widget)
    win._ctx.settings.update(widget_detect_clipboard=False)
    assert not monitor.enabled


def test_clipboard_text_that_is_not_a_url_is_ignored(qapp, win):
    widget = enable_widget(qapp, win)
    win._ctx.settings.update(widget_detect_clipboard=True)
    monitor = win.floating.clipboard
    monitor._clipboard.ownsClipboard = lambda: False
    QGuiApplication.clipboard().setText("minha senha secreta")
    monitor._on_changed()
    assert not widget.banner.isVisibleTo(widget)


# ------------------------------------------------------------------ local shortcuts, hints, keyboard
def test_hints_follow_shortcut_changes(qapp, win):
    widget = enable_widget(qapp, win)
    assert widget.analyze_caption.text() == "Ctrl + Shift + Enter"
    assert "Ctrl + Shift + Enter" in widget.analyze_button.toolTip()
    win._ctx.shortcuts.assign("widget.analyze", "Ctrl+Alt+A")
    assert widget.analyze_caption.text() == "Ctrl + Alt + A"
    assert "Ctrl + Alt + A" in widget.analyze_button.toolTip()
    assert "Ctrl + Enter" in win.home.add_button.toolTip()


def test_local_shortcut_objects_follow_the_manager(qapp, win):
    from PySide6.QtGui import QKeySequence

    def keys(binder):
        return {s.key().toString() for s in binder._shortcuts if s.isEnabled()}

    assert "Ctrl+H" in keys(win.shortcuts)
    win._ctx.shortcuts.assign("app.open_history", "Ctrl+Alt+H")
    assert "Ctrl+H" not in keys(win.shortcuts) and "Ctrl+Alt+H" in keys(win.shortcuts)
    win._ctx.shortcuts.disable("app.open_history")
    assert "Ctrl+Alt+H" not in keys(win.shortcuts)
    assert QKeySequence("Ctrl+Return").toString() in keys(win.shortcuts)


def test_manager_commands_reach_the_real_interface(qapp, win):
    manager = win._ctx.shortcuts
    assert manager.trigger("app.open_history")
    assert win.stack.currentWidget() is win.history
    assert manager.trigger("app.open_settings")
    assert win.stack.currentWidget() is win.settings_page
    widget = enable_widget(qapp, win)
    assert manager.trigger("widget.open_queue") and widget.queue_open
    assert manager.trigger("widget.minimize") and win.floating.compact


def test_enter_in_widget_url_field(qapp, win):
    widget = enable_widget(qapp, win)
    widget.url_input.setText("https://example.com/enterkey")
    widget.confirm()
    assert pump(qapp, widget.analysis_ready)
    widget.confirm()
    assert len(win._ctx.downloads.tasks()) == 1


def test_escape_hides_widget(qapp, win):
    widget = enable_widget(qapp, win)
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not win.floating.is_visible


# ------------------------------------------------------------------ Atalhos tab
def _key(key, modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def test_capture_dialog_validates_and_assigns(qapp, win, monkeypatch):
    from app.ui.pages import shortcuts_panel

    dialog = shortcuts_panel.ShortcutCaptureDialog(win, win._ctx.shortcuts, "app.open_history")
    dialog.keyPressEvent(_key(Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier))  # modifier only: waits
    assert dialog.result_text is None and "Ctrl" in dialog.keys.text()
    dialog.keyPressEvent(_key(Qt.Key.Key_H))  # single letter: friendly error, keeps waiting
    assert dialog.result_text is None and not dialog.error.isHidden()
    dialog.keyPressEvent(_key(Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
    assert dialog.result_text == "Ctrl+Alt+J"
    backspace = shortcuts_panel.ShortcutCaptureDialog(win, win._ctx.shortcuts, "app.open_history")
    backspace.keyPressEvent(_key(Qt.Key.Key_Backspace))
    assert backspace.result_text == shortcuts_panel.DISABLE


def test_edit_flow_with_conflict_dialog(qapp, win, monkeypatch):
    from app.ui.pages import shortcuts_panel

    class FakeDialog:
        def __init__(self, *args):
            self.result_text = "Ctrl+D"

        def exec(self):
            return True

    monkeypatch.setattr(shortcuts_panel, "ShortcutCaptureDialog", FakeDialog)
    asked = []
    monkeypatch.setattr(shortcuts_panel, "ask", lambda parent, title, message, buttons: asked.append(message) or "cancel")
    assert not shortcuts_panel.edit_shortcut(win, win._ctx, "app.open_history")
    assert "Abrir Downloads" in asked[0] and win._ctx.shortcuts.display("app.open_history") == "Ctrl + H"
    monkeypatch.setattr(shortcuts_panel, "ask", lambda *args: "replace")
    assert shortcuts_panel.edit_shortcut(win, win._ctx, "app.open_history")
    assert win._ctx.shortcuts.display("app.open_history") == "Ctrl + D"
    assert win._ctx.shortcuts.display("app.open_downloads") == "Desativado"


def test_shortcuts_tab_layout_intro_and_restore(qapp, win, monkeypatch):
    from app.ui.pages import shortcuts_panel

    win.navigate("settings:shortcuts")
    panel = win.settings_page.shortcuts_panel
    assert list(panel.sections) == ["app", "downloads", "widget", "system"]  # desktop and widget separated
    assert panel.rows["widget.toggle"].scope.text() == "GLOBAL"
    assert panel.rows["widget.focus_url"].scope.text() == "LOCAL"
    assert panel.rows["widget.toggle"].key.text() == "Ctrl + Shift + L"
    assert panel.intro.isVisibleTo(panel)
    panel.dismiss_intro()
    assert win._ctx.shortcuts_intro_state.load().completed
    win._ctx.shortcuts.assign("widget.toggle", "Ctrl+Alt+W")
    assert panel.rows["widget.toggle"].key.text() == "Ctrl + Alt + W"
    assert panel.rows["widget.toggle"].reset.isVisibleTo(panel.rows["widget.toggle"])
    theme_before = win._ctx.settings.settings.to_dict()
    monkeypatch.setattr(shortcuts_panel, "ask", lambda *args: "restore")
    panel.restore_defaults()
    assert panel.rows["widget.toggle"].key.text() == "Ctrl + Shift + L"
    assert win._ctx.settings.settings.to_dict() == theme_before  # nothing else changed


def test_widget_settings_tab_uses_the_same_shortcut_manager(qapp, win):
    win.navigate("settings:widget")
    page = win.settings_page
    assert page.widget_toggle_key.text() == "Ctrl + Shift + L"
    win._ctx.shortcuts.assign("widget.toggle", "Ctrl+Alt+K")
    assert page.widget_toggle_key.text() == "Ctrl + Alt + K"
    assert not page._rows["widget_always_on_top"].isEnabled()  # greyed out while the widget is off
    page.toggle("widget_enabled").setChecked(True)
    assert win._ctx.settings.settings.widget_enabled and page._rows["widget_always_on_top"].isEnabled()


def test_settings_tabs(qapp, win):
    page = win.settings_page
    keys = list(page.tabs)
    assert keys == ["general", "downloads", "widget", "shortcuts", "notifications", "history", "appearance",
                    "tutorial", "diagnostics", "updates", "about"]
    page.select_tab("diagnostics")
    assert page.tabs["diagnostics"].isVisibleTo(page) and not page.tabs["general"].isVisibleTo(page)
    assert page.reset_button.text() == "Restaurar aplicativo"


# ------------------------------------------------------------------ tour, tray, instance, startup
def test_widget_tour_runs_and_is_persisted(qapp, win):
    enable_widget(qapp, win)
    win._ctx.widget_tour_state.update(dont_show=False)
    win.floating.start_tour()
    tour = win.floating.tour
    seen = []
    while tour.running:
        seen.append(tour.current_id)
        tour.next()
    assert seen == [s.id for s in tour.steps] and len(seen) == 8
    assert win._ctx.widget_tour_state.load().completed
    assert not win.floating.widget.url_input.property("tourTarget")


def test_first_enable_offers_tour(qapp, win, monkeypatch):
    import app.ui.main_window as main_window_module

    win._ctx.widget_tour_state.update(dont_show=False)
    asked = []
    monkeypatch.setattr(main_window_module, "ask", lambda *args: asked.append(args[1]) or "later")
    enable_widget(qapp, win)
    assert pump(qapp, lambda: asked == ["Conheça o Widget do Luut"])
    assert not win._ctx.widget_tour_state.load().should_autostart


def test_tray_menu_and_widget_items(qapp, win):
    texts = [a.text() for a in win.tray.menu.actions() if a.text()]
    for expected in ("Abrir Luut", "Mostrar Widget", "Ocultar Widget", "Pausar todos", "Retomar todos",
                     "Abrir Downloads", "Abrir Histórico", "Configurações", "Sair"):
        assert expected in texts
    enable_widget(qapp, win)
    assert win.tray.hide_widget_action.isEnabled() and not win.tray.show_widget_action.isEnabled()
    win.tray.hide_widget_requested.emit()
    assert win.tray.show_widget_action.isEnabled()
    win.tray.page_requested.emit("history")
    assert win.stack.currentWidget() is win.history


def test_quit_from_tray_can_keep_running(qapp, win, monkeypatch):
    import app.ui.main_window as main_window_module

    win._ctx.provider.gate.clear()
    win._ctx.downloads.add(DownloadRequest(url="https://example.com/bg", output_dir=win._ctx.settings.settings.default_dir))
    assert pump(qapp, lambda: win._ctx.provider.running == 1)
    monkeypatch.setattr(main_window_module, "ask", lambda *args: "background")
    win._quit_from_tray()
    assert not win._quitting and win._ctx.downloads.has_running_work()
    win._ctx.provider.gate.set()


def test_closing_main_window_keeps_widget_running(qapp, win):
    from PySide6.QtGui import QCloseEvent

    enable_widget(qapp, win)
    event = QCloseEvent()
    win.closeEvent(event)
    if win.tray.available:
        assert not event.isAccepted() and not win._quitting


def test_second_launch_commands(qapp, win):
    win.hide()
    win.handle_instance_command("show")
    assert win.isVisible()
    enable_widget(qapp, win)
    win.floating.hide()
    win.handle_instance_command("widget")
    assert win.floating.is_visible


def test_single_instance_second_process_hands_over_its_command(qapp):
    """A real second process (like double-clicking the .exe again) talks to the running instance."""
    import subprocess
    import sys

    from app.system.single_instance import InstanceServer

    name = f"luut-test-{os.getpid()}-{time.monotonic_ns()}"
    server = InstanceServer(name)
    received = []
    server.command_received.connect(received.append)
    assert server.listening
    root = str(Path(__file__).resolve().parents[1])
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from PySide6.QtCore import QCoreApplication; "
            "app = QCoreApplication([]); from app.system.single_instance import notify_running_instance; "
            "raise SystemExit(0 if notify_running_instance(sys.argv[2], 'widget') else 3)")
    process = subprocess.Popen([sys.executable, "-c", code, root, name])
    assert pump(qapp, lambda: received == ["widget"], timeout=20)
    assert process.wait(10) == 0
    server.close()
    server.deleteLater()
    pump(qapp, timeout=0.2)


def test_start_with_windows_uses_the_run_key(qapp, win, fake_registry):
    win._ctx.settings.update(start_with_windows=True)
    assert fake_registry.value and fake_registry.value.endswith("--autostart")
    win._ctx.settings.update(start_with_windows=False, widget_start_with_windows=True)
    assert fake_registry.value  # the widget option also needs the entry
    win._ctx.settings.update(widget_start_with_windows=False)
    assert fake_registry.value is None


def test_autostart_shows_widget_only_if_configured(qapp, db, tmp_path, hotkeys):
    context = AppContext.create(db, FakeProvider())
    context.settings.update(temp_dir=str(tmp_path / "t"), widget_enabled=True, widget_start_with_windows=False)
    context.tutorial.set_dont_show(True)
    context.widget_tour_state.update(dont_show=True)
    window = MainWindow(context, hotkey_backend=hotkeys)
    window.start_up(autostart=True)
    assert not window.floating.is_visible
    context.settings.update(widget_start_with_windows=True)
    window.start_up(autostart=True)
    assert window.floating.is_visible
    dispose(qapp, window)


def test_main_tutorial_mentions_widget_and_shortcuts():
    from app.core.services.tutorial import TUTORIAL_STEPS

    ids = [s.id for s in sorted(TUTORIAL_STEPS, key=lambda s: s.order)]
    assert ids.index("widget") < ids.index("shortcuts") < ids.index("done")
    assert ids[0] == "welcome"
