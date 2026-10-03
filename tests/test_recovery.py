"""Persistence, crash recovery, .part integrity and resume behaviour."""

import errno
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.core.managers.download_manager import RESTART_NOTICE
from app.core.managers.settings_manager import SettingsManager
from app.core.models.download import DownloadStatus
from app.core.services.history import HistoryRepository
from app.infrastructure import filesystem as fs
from app.infrastructure.database import Database
from tests.conftest import ROOT, FakeProvider, make_request, wait_until


def status(manager, task_id):
    return manager.get(task_id).status


def test_new_user_starts_empty(make_manager, db):
    manager = make_manager()
    assert manager.restore() == []
    assert not manager.is_held
    assert manager.tasks() == []
    repo = HistoryRepository(db)
    assert repo.count() == 0 and repo.search() == []
    stats = repo.daily_stats()
    assert (stats.total, stats.completed, stats.active, stats.failed) == (0, 0, 0, 0)


def test_queue_of_five_survives_restart_in_order(make_manager, settings_manager, provider: FakeProvider, db):
    provider.gate.clear()
    manager = make_manager()
    ids = [manager.add(make_request(settings_manager, f"q{i}")).id for i in range(1, 6)]
    assert wait_until(lambda: provider.running == 1)
    manager.shutdown()  # app closed while the first one was downloading

    reopened = make_manager()
    restored = reopened.restore()
    assert [t.id for t in restored] == ids
    assert [t.status for t in restored] == [DownloadStatus.INTERRUPTED] + [DownloadStatus.QUEUED] * 4
    assert reopened.is_held and provider.running == 0
    order: list[str] = []
    reopened.add_listener(lambda event, task: order.append(task.id)
                          if task.status == DownloadStatus.COMPLETED and task.id not in order else None)
    provider.gate.set()
    reopened.resume_all()
    assert wait_until(lambda: all(status(reopened, i) == DownloadStatus.COMPLETED for i in ids), 10)
    assert order == ids  # the interrupted one first, then the queue in its original order


def test_no_artificial_queue_limit(make_manager, settings_manager, provider: FakeProvider):
    settings_manager.update(max_concurrent=2)
    provider.gate.clear()
    manager = make_manager()
    for i in range(500):
        manager.add(make_request(settings_manager, f"bulk{i}"))
    assert wait_until(lambda: provider.running == 2)
    assert len(manager.tasks()) == 500
    assert len(manager.queue_positions()) == 498
    assert provider.max_running == 2
    manager.shutdown(cancel=True)
    provider.gate.set()


def test_progress_checkpoints_are_persisted(make_manager, settings_manager, provider: FakeProvider, db):
    provider.delay = 0.03
    manager = make_manager(checkpoint_interval=0.0)
    task = manager.add(make_request(settings_manager, "ckpt"))
    repo = HistoryRepository(db)
    assert wait_until(lambda: (repo.get(task.id).progress.downloaded_bytes or 0) >= 3000)
    assert repo.get(task.id).progress.total_bytes == 10_000
    manager.pause(task.id)


def test_pause_and_exit_keeps_paused_state(make_manager, settings_manager, provider: FakeProvider, db):
    provider.delay = 0.05
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "pexit"))
    assert wait_until(lambda: manager.get(task.id).progress.downloaded_bytes >= 2000)
    manager.shutdown(pause=True)
    saved = HistoryRepository(db).get(task.id)
    assert saved.status == DownloadStatus.PAUSED
    reopened = make_manager()
    [restored] = reopened.restore()
    assert restored.status == DownloadStatus.PAUSED and restored.has_partial_data
    assert restored.progress.downloaded_bytes >= 2000


def test_discard_recoverable(make_manager, settings_manager, provider: FakeProvider):
    provider.gate.clear()
    manager = make_manager()
    ids = [manager.add(make_request(settings_manager, f"d{i}")).id for i in range(3)]
    assert wait_until(lambda: provider.running == 1)
    manager.shutdown()
    provider.gate.set()
    reopened = make_manager()
    reopened.restore()
    assert reopened.discard_recoverable() == 3
    assert all(status(reopened, i) == DownloadStatus.CANCELLED for i in ids)
    assert not reopened.is_held
    assert not any(reopened.work_dir(i).exists() for i in ids)


def test_incomplete_engine_output_is_never_completed(make_manager, settings_manager, provider: FakeProvider):
    provider.finish_as_part = True
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "broken"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    assert manager.get(task.id).file_path is None
    assert not any(Path(settings_manager.settings.default_dir).glob("*.mp4"))


def test_final_file_only_appears_after_complete_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "media.mp4"
    source.write_bytes(b"x" * 100)
    destination = tmp_path / "out"

    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(AppError):
        fs.move_to_destination(source, destination, "Vídeo", overwrite=False)
    assert not (destination / "Vídeo.mp4").exists()
    assert not (destination / "Vídeo.mp4.part").exists()
    assert source.stat().st_size == 100  # finished data kept in the temp folder for "Tentar novamente"
    monkeypatch.undo()
    target = fs.move_to_destination(source, destination, "Vídeo", overwrite=False)
    assert target.name == "Vídeo.mp4" and target.stat().st_size == 100
    assert list(destination.iterdir()) == [target]


def test_source_without_resume_support_is_reported(make_manager, settings_manager, provider: FakeProvider):
    provider.chunk_size = 300_000  # 3 MB file so the partial data is significant
    provider.delay = 0.05
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "norange"))
    assert wait_until(lambda: manager.get(task.id).progress.downloaded_bytes >= 1_500_000, 10)
    manager.pause(task.id)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.PAUSED)
    provider.honor_resume = False
    notices = []
    manager.add_listener(lambda event, t: notices.append(t.notice) if t.notice else None)
    manager.resume(task.id)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED, 10)
    assert RESTART_NOTICE in notices


CRASH_SCRIPT = textwrap.dedent('''
    import os, sys, time
    sys.path.insert(0, {root!r})
    from app.core.managers.download_manager import DownloadManager
    from app.core.managers.settings_manager import SettingsManager
    from app.core.services.history import HistoryRepository
    from app.infrastructure.database import Database
    from tests.conftest import FakeProvider, make_request

    db = Database({db!r})
    settings = SettingsManager(db)
    settings.update(default_dir={out!r}, temp_dir={temp!r})
    provider = FakeProvider(delay=0.05)
    repo = HistoryRepository(db)
    manager = DownloadManager(provider, repo, lambda: settings.settings, checkpoint_interval=0.0)
    ids = [manager.add(make_request(settings, "crash%d" % i)).id for i in range(3)]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        saved = repo.get(ids[0])
        if saved.progress.downloaded_bytes >= 3000:
            break
        time.sleep(0.02)
    print(ids[0], flush=True)
    os._exit(9)  # simulated crash: no cleanup, no shutdown, no final writes
''')


def test_simulated_crash_recovery(tmp_path: Path):
    db_path, out, temp = tmp_path / "crash.db", tmp_path / "Vídeos", tmp_path / "temp"
    script = CRASH_SCRIPT.format(root=str(ROOT), db=str(db_path), out=str(out), temp=str(temp))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 9, result.stderr
    first_id = result.stdout.strip()

    db = Database(db_path)
    settings = SettingsManager(db)
    provider = FakeProvider()
    from app.core.managers.download_manager import DownloadManager
    manager = DownloadManager(provider, HistoryRepository(db), lambda: settings.settings)
    restored = {t.id: t for t in manager.restore()}
    assert len(restored) == 3                                        # queue restored
    assert restored[first_id].status == DownloadStatus.INTERRUPTED   # running task detected as interrupted
    assert restored[first_id].has_partial_data                       # .part kept for resuming
    assert restored[first_id].progress.downloaded_bytes >= 3000      # last checkpoint
    others = [t for i, t in restored.items() if i != first_id]
    assert all(t.status == DownloadStatus.QUEUED for t in others)
    assert manager.is_held
    manager.resume_all()
    assert wait_until(lambda: all(manager.get(i).status == DownloadStatus.COMPLETED for i in restored), 15)
    assert provider.resumed_from[restored[first_id].url] >= 3000     # continued, not restarted
    assert all(Path(manager.get(i).file_path).stat().st_size == 10_000 for i in restored)
    manager.shutdown()
    db.close()


def test_settings_survive_restart(tmp_path: Path):
    db = Database(tmp_path / "s.db")
    SettingsManager(db).update(max_concurrent=3, theme="light")
    db.close()
    reopened = Database(tmp_path / "s.db")
    settings = SettingsManager(reopened).settings
    assert (settings.max_concurrent, settings.theme) == (3, "light")
    reopened.close()
