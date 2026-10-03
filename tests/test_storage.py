import csv
import json
from datetime import date

from app.core.managers.settings_manager import SettingsManager
from app.core.models.download import DownloadStatus, DownloadTask
from app.core.models.settings import AppSettings
from app.core.services.history import HistoryRepository, export_csv, export_json
from app.infrastructure.database import Database


def test_database_creates_schema(db: Database):
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "downloads"} <= tables
    assert db.integrity_ok()


def test_settings_roundtrip_and_persistence(db: Database):
    manager = SettingsManager(db)
    changes = []
    manager.add_listener(lambda s, keys: changes.append(keys))
    manager.update(max_concurrent=3, organize_enabled=True, default_dir="D:/Vídeos")
    assert changes == [{"max_concurrent", "organize_enabled", "default_dir"}]
    reloaded = SettingsManager(db).settings
    assert reloaded.max_concurrent == 3
    assert reloaded.organize_enabled is True
    assert reloaded.default_dir == "D:/Vídeos"


def test_settings_validation_rejects_bad_values():
    settings = AppSettings.from_dict({"max_concurrent": 50, "theme": "neon", "organize_enabled": "yes",
                                      "history_limit": 7, "unknown": 1})
    assert settings.max_concurrent == 1
    assert settings.theme == "dark"
    assert settings.organize_enabled is False
    assert settings.history_limit == 500


def test_settings_no_change_no_event(db: Database):
    manager = SettingsManager(db)
    calls = []
    manager.add_listener(lambda s, keys: calls.append(keys))
    manager.update(theme="dark")
    assert calls == []


def _task(title: str, status: DownloadStatus, **kw) -> DownloadTask:
    return DownloadTask(url=f"https://ex.com/{title}", output_dir="C:/out", title=title, status=status, **kw)


def test_history_search_and_filters(db: Database):
    repo = HistoryRepository(db)
    repo.save(_task("Alpha", DownloadStatus.COMPLETED, finished_at="2026-01-01T10:00:00"))
    repo.save(_task("Beta", DownloadStatus.FAILED, finished_at="2026-01-02T10:00:00"))
    repo.save(_task("Gamma", DownloadStatus.CANCELLED, finished_at="2026-01-03T10:00:00"))
    repo.save(_task("Queued", DownloadStatus.QUEUED))
    assert [t.title for t in repo.search()] == ["Gamma", "Beta", "Alpha"]
    assert [t.title for t in repo.search(filter_key="completed")] == ["Alpha"]
    assert [t.title for t in repo.search(filter_key="failed")] == ["Beta"]
    assert [t.title for t in repo.search(filter_key="cancelled")] == ["Gamma"]
    assert [t.title for t in repo.search("alp")] == ["Alpha"]
    assert [t.title for t in repo.unfinished()] == ["Queued"]


def test_history_clear_keeps_unfinished(db: Database):
    repo = HistoryRepository(db)
    repo.save(_task("Done", DownloadStatus.COMPLETED))
    repo.save(_task("Waiting", DownloadStatus.PAUSED))
    removed = repo.clear()
    assert [t.title for t in removed] == ["Done"]
    assert repo.search() == []
    assert len(repo.unfinished()) == 1


def test_history_prune(db: Database):
    repo = HistoryRepository(db)
    for i in range(5):
        repo.save(_task(f"T{i}", DownloadStatus.COMPLETED, finished_at=f"2026-01-0{i + 1}T00:00:00"))
    removed = repo.prune(2)
    assert {t.title for t in removed} == {"T0", "T1", "T2"}
    assert [t.title for t in repo.search()] == ["T4", "T3"]


def test_daily_stats(db: Database):
    repo = HistoryRepository(db)
    today = date.today().isoformat()
    repo.save(_task("A", DownloadStatus.COMPLETED, created_at=f"{today}T10:00:00"))
    repo.save(_task("B", DownloadStatus.FAILED, created_at=f"{today}T11:00:00"))
    repo.save(_task("C", DownloadStatus.DOWNLOADING, created_at=f"{today}T12:00:00"))
    repo.save(_task("D", DownloadStatus.COMPLETED, created_at="2020-01-01T00:00:00"))
    stats = repo.daily_stats()
    assert (stats.total, stats.completed, stats.active, stats.failed) == (3, 1, 1, 1)


def test_export_csv_and_json(tmp_path, db: Database):
    tasks = [_task("Vídeo; com \"aspas\"", DownloadStatus.COMPLETED, file_path="C:/out/v.mp4", file_size=10)]
    csv_path = tmp_path / "h.csv"
    json_path = tmp_path / "h.json"
    export_csv(tasks, csv_path)
    export_json(tasks, json_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0]["title"] == "Vídeo; com \"aspas\""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data[0]["status"] == "Concluído"
    assert "id" not in data[0] and "thumbnail_path" not in data[0]
