"""First launch, tutorial state, schema migration, reset and --fresh-start."""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.core.services.tutorial import (
    TUTORIAL_STEPS, Placement, TutorialManager, TutorialStateRepository, ordered_steps,
)
from app.infrastructure.app_data import activate_fresh_profile, fresh_profile_dir, wipe_app_data
from app.infrastructure.database import SCHEMA_VERSION, Database
from app.utils.paths import APP_DATA_ENV, base_data_dir, is_fresh_profile


def test_steps_are_declarative_and_complete():
    steps = ordered_steps()
    ids = [s.id for s in steps]
    assert ids[0] == "welcome" and ids[-1] == "done"
    assert {"sidebar", "url", "analyze", "info", "quality_format", "destination", "queue", "downloads",
            "history", "settings", "recovery"} <= set(ids)
    assert len(set(ids)) == len(ids)
    assert all(s.title and s.description for s in steps)
    assert all(s.target is None for s in steps if s.placement == Placement.CENTER)
    assert [s.order for s in steps] == sorted(s.order for s in TUTORIAL_STEPS)


def test_fresh_database_autostarts_tutorial(db: Database):
    assert TutorialManager(TutorialStateRepository(db)).should_autostart()


def test_completed_tutorial_does_not_autostart(db: Database):
    manager = TutorialManager(TutorialStateRepository(db))
    manager.start()
    while manager.next() is not None:
        pass
    assert not manager.running
    state = manager.state
    assert state.completed and state.completed_at and not state.should_autostart


def test_skipped_tutorial_returns_next_launch(db: Database):
    manager = TutorialManager(TutorialStateRepository(db))
    manager.start()
    manager.next()
    manager.finish(completed=False)
    assert manager.state.last_step == "sidebar"
    assert TutorialManager(TutorialStateRepository(db)).should_autostart()


def test_dont_show_again_and_reactivation(db: Database):
    manager = TutorialManager(TutorialStateRepository(db))
    manager.start()
    manager.set_dont_show(True)
    manager.finish(completed=False)
    assert not TutorialManager(TutorialStateRepository(db)).should_autostart()
    manager.reactivate()
    assert TutorialManager(TutorialStateRepository(db)).should_autostart()


def test_back_navigation(db: Database):
    manager = TutorialManager(TutorialStateRepository(db))
    manager.start()
    manager.back()
    assert manager.is_first
    manager.next()
    manager.next()
    assert manager.back().id == "sidebar"


def _v1_database(path: Path, first_run_done: bool | None) -> None:
    from app.infrastructure.database import _SCHEMA_V1
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_V1)
    conn.execute("PRAGMA user_version=1")
    if first_run_done is not None:
        conn.execute("INSERT INTO settings VALUES ('first_run_done', ?)", (json.dumps(first_run_done),))
    conn.execute("INSERT INTO downloads (id, url, container, output_dir, status, created_at) "
                 "VALUES ('a', 'https://x.com', 'mp4', 'C:/v', 'completed', '2026-01-01T00:00:00')")
    conn.commit()
    conn.close()


def test_migration_keeps_existing_users_out_of_the_tutorial(tmp_path: Path):
    _v1_database(tmp_path / "old.db", first_run_done=True)
    db = Database(tmp_path / "old.db")
    assert db.query_one("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert not TutorialManager(TutorialStateRepository(db)).should_autostart()
    assert db.query_one("SELECT * FROM settings WHERE key = 'first_run_done'") is None
    row = db.query_one("SELECT downloaded_bytes, total_bytes FROM downloads WHERE id = 'a'")
    assert row["downloaded_bytes"] == 0 and row["total_bytes"] is None
    db.close()


def test_migration_without_previous_first_run_shows_tutorial(tmp_path: Path):
    _v1_database(tmp_path / "old.db", first_run_done=None)
    db = Database(tmp_path / "old.db")
    assert TutorialManager(TutorialStateRepository(db)).should_autostart()
    db.close()


def test_reset_wipes_only_app_data(tmp_path: Path):
    data = tmp_path / "LuutVideoDownloader"
    videos = tmp_path / "Meus Vídeos"
    custom_temp = tmp_path / "TempCustom"
    for folder in ("logs", "thumbnails", "temp", "cache"):
        (data / folder).mkdir(parents=True)
        (data / folder / "x.bin").write_bytes(b"1")
    videos.mkdir()
    (videos / "Meu vídeo.mp4").write_bytes(b"video")
    task_dir = custom_temp / ("a" * 32)
    task_dir.mkdir(parents=True)
    (task_dir / "media.mp4.part").write_bytes(b"p")
    (custom_temp / "outra pasta do usuário").mkdir()
    (data / "notes.txt").write_text("não é do app")
    db = Database(data / "luut.db")
    db.execute("INSERT INTO settings VALUES ('temp_dir', ?)", (json.dumps(str(custom_temp)),))
    db.close()

    assert wipe_app_data(data) == []
    assert not (data / "luut.db").exists()
    assert not any((data / f).exists() for f in ("logs", "thumbnails", "temp", "cache"))
    assert (videos / "Meu vídeo.mp4").read_bytes() == b"video"      # downloaded videos untouched
    assert (data / "notes.txt").exists()                           # unknown files untouched
    assert not task_dir.exists() and (custom_temp / "outra pasta do usuário").exists()
    fresh = Database(data / "luut.db")                             # next launch = brand-new state
    assert TutorialManager(TutorialStateRepository(fresh)).should_autostart()
    fresh.close()


def test_fresh_start_profile_is_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))  # macOS keeps the profile under ~/Library
    monkeypatch.delenv(APP_DATA_ENV, raising=False)
    real = base_data_dir()
    real.mkdir(parents=True)
    (real / "luut.db").write_bytes(b"real data")
    stale = fresh_profile_dir()
    stale.mkdir(parents=True)
    (stale / "luut.db").write_bytes(b"old test run")

    folder = activate_fresh_profile()
    assert os.environ[APP_DATA_ENV] == str(folder)
    assert folder != real and not (folder / "luut.db").exists()
    assert is_fresh_profile()
    assert (real / "luut.db").read_bytes() == b"real data"
