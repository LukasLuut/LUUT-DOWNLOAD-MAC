"""ShortcutManager: parsing, validation, conflicts, persistence, restore, dispatch."""

import pytest

from app.core.shortcuts import (
    DEFAULT_ACTIONS,
    DISABLED_TEXT,
    ShortcutConflict,
    ShortcutError,
    ShortcutManager,
    ShortcutRepository,
    parse_combo,
    validate_for,
)


@pytest.fixture
def manager(db):
    return ShortcutManager(ShortcutRepository(db))


def test_every_default_is_valid_and_conflict_free(manager):
    ids = [a.id for a in DEFAULT_ACTIONS]
    assert len(ids) == len(set(ids))
    for action in DEFAULT_ACTIONS:
        if action.default:
            combo = parse_combo(action.default)
            validate_for(action, combo)
            assert manager.conflict(action.id, combo) is None, action.id


def test_parse_and_display():
    combo = parse_combo(" ctrl +  shift + l ")
    assert combo.text == "Ctrl+Shift+L"
    assert combo.display == "Ctrl + Shift + L"
    assert parse_combo("Shift+Ctrl+Return").text == "Ctrl+Shift+Enter"
    assert parse_combo("Meta+Alt+F5").text == "Alt+Win+F5"
    assert parse_combo("Ctrl+,").text == "Ctrl+,"


@pytest.mark.parametrize("text,code", [
    ("", "empty"), ("Ctrl+Shift", "modifier_only"), ("Ctrl", "modifier_only"), ("Ctrl+Ç", "invalid_key"),
    ("Ctrl+A+B", "invalid_key"), ("Ctrl++", "invalid_key"),
])
def test_invalid_combinations(text, code):
    with pytest.raises(ShortcutError) as err:
        parse_combo(text)
    assert err.value.code == code
    assert err.value.message


def test_rules_per_action(manager):
    with pytest.raises(ShortcutError) as err:
        manager.validate("widget.toggle", "Ctrl+L")  # global with a single modifier: too generic
    assert err.value.code == "global_too_generic"
    with pytest.raises(ShortcutError):
        manager.validate("widget.toggle", "Enter")
    with pytest.raises(ShortcutError) as err:
        manager.validate("app.open_history", "H")  # would type text
    assert err.value.code == "needs_modifier"
    with pytest.raises(ShortcutError) as err:
        manager.validate("app.open_history", "Alt+F4")
    assert err.value.code == "reserved"
    with pytest.raises(ShortcutError):
        manager.validate("app.open_history", "Win+D")
    with pytest.raises(ShortcutError):
        manager.validate("app.open_history", "Ctrl+C")  # text editing
    assert manager.validate("app.paste_url", "Ctrl+V").text == "Ctrl+V"  # the one command allowed to use it
    assert manager.validate("app.open_history", "Alt+D").text == "Alt+D"
    assert manager.validate("app.open_history", "F7").text == "F7"
    assert manager.validate("app.analyze", "Enter").text == "Enter"  # single key: local, allowed here
    assert manager.validate("widget.toggle", "Ctrl+Alt+Enter").text == "Ctrl+Alt+Enter"


def test_registration_and_trigger(manager):
    calls = []
    manager.register_handler("app.open_history", lambda: calls.append("history"))
    assert manager.trigger("app.open_history")
    assert calls == ["history"]
    assert manager.bindings_for_window("app")["Ctrl+H"] == ["app.open_history"]


def test_change_removes_old_and_registers_new(manager):
    manager.assign("app.open_history", "Ctrl+Alt+H")
    bindings = manager.bindings_for_window("app")
    assert "Ctrl+H" not in bindings
    assert bindings["Ctrl+Alt+H"] == ["app.open_history"]
    assert manager.display("app.open_history") == "Ctrl + Alt + H"
    assert not manager.is_default("app.open_history")


def test_conflict_is_rejected_then_replaced(manager):
    with pytest.raises(ShortcutConflict) as err:
        manager.assign("app.open_history", "Ctrl+D")  # Abrir Downloads
    assert err.value.other.id == "app.open_downloads"
    assert "Abrir Downloads" in err.value.message
    assert manager.display("app.open_history") == "Ctrl + H"  # unchanged
    manager.assign("app.open_history", "Ctrl+D", replace=True)
    assert manager.display("app.open_history") == "Ctrl + D"
    assert manager.display("app.open_downloads") == DISABLED_TEXT


def test_global_conflicts_everywhere_but_locals_only_in_their_window(manager):
    with pytest.raises(ShortcutConflict):
        manager.assign("app.open_history", "Ctrl+Shift+L")  # already the global widget toggle
    with pytest.raises(ShortcutConflict):
        manager.assign("app.toggle", "Ctrl+Shift+L")  # two global commands, same keys: never allowed
    # The same local combination can exist in the main window and in the widget.
    manager.assign("widget.open_history", "Ctrl+H")
    assert manager.display("app.open_history") == "Ctrl + H"
    assert manager.display("widget.open_history") == "Ctrl + H"


def test_shared_combination_runs_the_command_that_applies(manager):
    order = []
    manager.register_handler("widget.analyze", lambda: order.append("analyze") or False)  # not applicable
    manager.register_handler("widget.start_download", lambda: order.append("start"))
    ids = manager.bindings_for_window("widget")["Ctrl+Shift+Enter"]
    assert set(ids) == {"widget.analyze", "widget.start_download"}
    assert manager.trigger_first(ids) == "widget.start_download"
    assert order == ["analyze", "start"]


def test_disabled_shortcut_does_not_execute(manager):
    calls = []
    manager.register_handler("app.open_history", lambda: calls.append(1))
    manager.disable("app.open_history")
    assert not manager.trigger("app.open_history")
    assert calls == []
    assert manager.display("app.open_history") == DISABLED_TEXT
    assert "Ctrl+H" not in manager.bindings_for_window("app")


def test_persistence_across_restarts(db):
    first = ShortcutManager(ShortcutRepository(db))
    first.assign("widget.toggle", "Ctrl+Alt+W")
    first.disable("app.open_history")
    reopened = ShortcutManager(ShortcutRepository(db))
    assert reopened.display("widget.toggle") == "Ctrl + Alt + W"
    assert reopened.display("app.open_history") == DISABLED_TEXT
    row = db.query_one("SELECT * FROM shortcuts WHERE action_id = 'widget.toggle'")
    assert row["key_combination"] == "Ctrl+Alt+W" and row["is_global"] == 1 and row["scope"] == "widget"
    assert row["enabled"] == 1 and row["updated_at"]


def test_restore_defaults(db):
    manager = ShortcutManager(ShortcutRepository(db))
    manager.assign("widget.toggle", "Ctrl+Alt+W")
    manager.disable("app.open_history")
    changed = []
    manager.add_listener(changed.append)
    manager.restore_defaults()
    assert all(manager.is_default(a.id) for a in DEFAULT_ACTIONS)
    assert changed and changed[0] == {a.id for a in DEFAULT_ACTIONS}
    assert ShortcutManager(ShortcutRepository(db)).display("widget.toggle") == "Ctrl + Shift + L"


def test_reset_single_action(manager):
    manager.assign("app.open_history", "Ctrl+Alt+H")
    manager.reset("app.open_history")
    assert manager.is_default("app.open_history")


def test_capturing_blocks_every_command(manager):
    calls = []
    manager.register_handler("app.open_history", lambda: calls.append(1))
    manager.set_capturing(True)
    assert not manager.trigger("app.open_history")
    manager.set_capturing(False)
    assert manager.trigger("app.open_history") and calls == [1]


def test_invalid_stored_value_falls_back_to_default(db):
    ShortcutRepository(db).save(next(a for a in DEFAULT_ACTIONS if a.id == "widget.toggle"), "Ctrl+L", True)
    assert ShortcutManager(ShortcutRepository(db)).display("widget.toggle") == "Ctrl + Shift + L"


def test_hint_text_follows_changes(manager):
    assert manager.hint("widget.analyze") == "Analisar URL\nAtalho: Ctrl + Shift + Enter"
    manager.disable("widget.analyze")
    assert manager.hint("widget.analyze") == "Analisar URL"
