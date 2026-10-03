import errno
from pathlib import Path

import pytest

from app.core.errors import ErrorKind
from app.core.managers.download_manager import DownloadEvent, DownloadManager
from app.core.models.download import DownloadStatus
from app.core.services.history import HistoryRepository
from tests.conftest import FakeProvider, make_request, wait_until


def status(manager: DownloadManager, task_id: str) -> DownloadStatus:
    return manager.get(task_id).status


def test_download_completes_and_is_saved(make_manager, settings_manager, db):
    manager = make_manager()
    events = []
    manager.add_listener(lambda event, task: events.append(event))
    task = manager.add(make_request(settings_manager, "a", title="Meu: vídeo?"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    done = manager.get(task.id)
    path = Path(done.file_path)
    assert path.is_file() and path.name == "Meu vídeo.mp4"
    assert done.file_size == 10_000
    assert not manager.work_dir(task.id).exists()
    assert HistoryRepository(db).get(task.id).status == DownloadStatus.COMPLETED
    assert DownloadEvent.PROGRESS in events and events[-1] == DownloadEvent.FINISHED


def test_duplicate_names_never_overwrite(make_manager, settings_manager):
    manager = make_manager()
    ids = [manager.add(make_request(settings_manager, f"d{i}", title="Mesmo nome")).id for i in range(3)]
    assert wait_until(lambda: all(status(manager, i) == DownloadStatus.COMPLETED for i in ids))
    names = sorted(Path(manager.get(i).file_path).name for i in ids)
    assert names == ["Mesmo nome (1).mp4", "Mesmo nome (2).mp4", "Mesmo nome.mp4"]


def test_overwrite_policy(make_manager, settings_manager):
    settings_manager.update(existing_file_policy="overwrite")
    manager = make_manager()
    first = manager.add(make_request(settings_manager, "o1", title="Igual"))
    assert wait_until(lambda: status(manager, first.id) == DownloadStatus.COMPLETED)
    second = manager.add(make_request(settings_manager, "o2", title="Igual"))
    assert wait_until(lambda: status(manager, second.id) == DownloadStatus.COMPLETED)
    assert manager.get(first.id).file_path == manager.get(second.id).file_path


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_concurrency_limit_is_respected(make_manager, settings_manager, provider: FakeProvider, limit):
    settings_manager.update(max_concurrent=limit)
    provider.delay = 0.02
    manager = make_manager()
    ids = [manager.add(make_request(settings_manager, f"c{i}")).id for i in range(4)]
    assert wait_until(lambda: all(status(manager, i) == DownloadStatus.COMPLETED for i in ids), 15)
    assert provider.max_running == limit


def test_independent_progress_and_individual_cancel(make_manager, settings_manager, provider: FakeProvider):
    settings_manager.update(max_concurrent=2)
    provider.gate.clear()
    manager = make_manager()
    a = manager.add(make_request(settings_manager, "p1"))
    b = manager.add(make_request(settings_manager, "p2"))
    c = manager.add(make_request(settings_manager, "p3"))
    assert wait_until(lambda: provider.running == 2)
    assert status(manager, c.id) == DownloadStatus.QUEUED
    assert manager.queue_position(c.id) == 1
    manager.cancel(a.id)
    provider.gate.set()
    assert wait_until(lambda: status(manager, a.id) == DownloadStatus.CANCELLED)
    assert wait_until(lambda: status(manager, b.id) == DownloadStatus.COMPLETED)
    assert wait_until(lambda: status(manager, c.id) == DownloadStatus.COMPLETED)
    assert not manager.work_dir(a.id).exists()
    assert manager.get(a.id).file_path is None


def test_cancel_queued_task(make_manager, settings_manager, provider: FakeProvider):
    provider.gate.clear()
    manager = make_manager()
    manager.add(make_request(settings_manager, "q1"))
    queued = manager.add(make_request(settings_manager, "q2"))
    assert manager.cancel(queued.id)
    assert status(manager, queued.id) == DownloadStatus.CANCELLED
    provider.gate.set()


def test_pause_keeps_partial_and_resume_continues(make_manager, settings_manager, provider: FakeProvider):
    provider.delay = 0.03
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "pause"))
    assert wait_until(lambda: manager.get(task.id).progress.downloaded_bytes >= 3000)
    assert manager.pause(task.id)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.PAUSED)
    paused = manager.get(task.id)
    assert paused.has_partial_data
    assert manager.resume(task.id)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    assert provider.resumed_from[task.url] >= 3000
    assert Path(manager.get(task.id).file_path).stat().st_size == 10_000


def test_transient_failure_retries_then_succeeds(make_manager, settings_manager, provider: FakeProvider):
    manager = make_manager()
    request = make_request(settings_manager, "flaky")
    provider.failures[request.url] = [ErrorKind.NETWORK, ErrorKind.NETWORK]
    task = manager.add(request)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    assert provider.calls[request.url] == 3


def test_gives_up_after_three_attempts(make_manager, settings_manager, provider: FakeProvider):
    manager = make_manager()
    request = make_request(settings_manager, "down")
    provider.failures[request.url] = [ErrorKind.NETWORK] * 5
    task = manager.add(request)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    failed = manager.get(task.id)
    assert provider.calls[request.url] == 3
    assert failed.error_message == "A conexão foi interrompida. Tente novamente."
    assert manager.resume(task.id)  # "Tentar novamente"
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)


def test_permanent_error_does_not_retry(make_manager, settings_manager, provider: FakeProvider):
    manager = make_manager()
    request = make_request(settings_manager, "gone")
    provider.failures[request.url] = [ErrorKind.UNAVAILABLE]
    task = manager.add(request)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    assert provider.calls[request.url] == 1
    assert manager.get(task.id).error_kind == ErrorKind.UNAVAILABLE.value


def test_missing_destination_folder_is_created(make_manager, settings_manager, tmp_path):
    manager = make_manager()
    target = tmp_path / "nova pasta" / "sub pasta çã"
    request = make_request(settings_manager, "mk", output_dir=str(target))
    task = manager.add(request)
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    assert Path(manager.get(task.id).file_path).parent == target


def test_permission_error_fails_friendly(make_manager, settings_manager, monkeypatch):
    from app.infrastructure import filesystem as fs

    def deny(path):
        raise fs.classify_os_error(PermissionError(errno.EACCES, "denied"))

    monkeypatch.setattr(fs, "ensure_writable_dir", deny)
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "perm"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    assert manager.get(task.id).error_message == "O aplicativo não possui permissão para salvar nesta pasta."


def test_insufficient_disk_space(make_manager, settings_manager, monkeypatch):
    from app.infrastructure import filesystem as fs
    monkeypatch.setattr(fs, "free_space", lambda path: 100)
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "big", estimated_size=10_000_000))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    assert manager.get(task.id).error_kind == ErrorKind.DISK_FULL.value


def test_disk_full_during_move(make_manager, settings_manager, monkeypatch):
    from app.infrastructure import filesystem as fs

    def full(*args, **kwargs):
        raise fs.classify_os_error(OSError(errno.ENOSPC, "No space left on device"))

    monkeypatch.setattr(fs, "move_to_destination", full)
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "full"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.FAILED)
    assert manager.get(task.id).error_message == "Não há espaço suficiente no disco selecionado."


def test_queue_reordering(make_manager, settings_manager, provider: FakeProvider):
    provider.gate.clear()
    manager = make_manager()
    manager.add(make_request(settings_manager, "running"))
    a = manager.add(make_request(settings_manager, "a"))
    b = manager.add(make_request(settings_manager, "b"))
    c = manager.add(make_request(settings_manager, "c"))
    assert manager.move(c.id, -1)
    assert [manager.queue_position(i) for i in (a.id, c.id, b.id)] == [1, 2, 3]
    assert manager.move_to_top(b.id)
    assert manager.queue_position(b.id) == 1
    assert not manager.move(b.id, -1)
    provider.gate.set()


def test_remove_and_clear_finished(make_manager, settings_manager, provider: FakeProvider, db):
    manager = make_manager()
    done = manager.add(make_request(settings_manager, "r1"))
    assert wait_until(lambda: status(manager, done.id) == DownloadStatus.COMPLETED)
    provider.gate.clear()
    manager.add(make_request(settings_manager, "r2"))
    waiting = manager.add(make_request(settings_manager, "r3"))
    assert manager.remove(waiting.id)
    assert manager.get(waiting.id) is None
    assert HistoryRepository(db).get(waiting.id).status == DownloadStatus.CANCELLED
    assert manager.clear_finished() == 1
    assert manager.get(done.id) is None
    assert HistoryRepository(db).get(done.id) is not None  # history keeps the record
    provider.gate.set()


def test_history_disabled_does_not_keep_records(make_manager, settings_manager, db):
    settings_manager.update(history_enabled=False)
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "nohist"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    assert HistoryRepository(db).get(task.id) is None


def test_organize_downloads(make_manager, settings_manager):
    settings_manager.update(organize_enabled=True, organize_pattern="uploader")
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "org", uploader="Canal: Teste"))
    assert wait_until(lambda: status(manager, task.id) == DownloadStatus.COMPLETED)
    assert Path(manager.get(task.id).file_path).parent.name == "Canal Teste"


def test_close_during_download_marks_interrupted_and_restores(make_manager, settings_manager, provider: FakeProvider, db):
    provider.delay = 0.05
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "close"))
    queued = manager.add(make_request(settings_manager, "close2"))
    assert wait_until(lambda: manager.get(task.id).progress.downloaded_bytes >= 2000)
    manager.shutdown()
    assert HistoryRepository(db).get(task.id).status == DownloadStatus.INTERRUPTED

    reopened = make_manager()
    restored = {t.id: t for t in reopened.restore()}
    assert restored[task.id].status == DownloadStatus.INTERRUPTED
    assert restored[task.id].has_partial_data
    assert restored[queued.id].status == DownloadStatus.QUEUED  # waiting items keep waiting
    assert reopened.is_held and provider.running == 0  # nothing starts before the user decides
    assert reopened.resume(task.id)
    assert wait_until(lambda: status(reopened, task.id) == DownloadStatus.COMPLETED)
    assert provider.resumed_from[task.url] >= 2000


def test_shutdown_with_cancel(make_manager, settings_manager, provider: FakeProvider, db):
    provider.delay = 0.05
    manager = make_manager()
    task = manager.add(make_request(settings_manager, "sc"))
    queued = manager.add(make_request(settings_manager, "sc2"))
    assert wait_until(lambda: provider.running == 1)
    manager.shutdown(cancel=True)
    repo = HistoryRepository(db)
    assert repo.get(task.id).status == DownloadStatus.CANCELLED
    assert repo.get(queued.id).status == DownloadStatus.CANCELLED


def test_orphan_temp_dirs_are_cleaned(make_manager, settings_manager):
    manager = make_manager()
    orphan = Path(settings_manager.settings.temp_dir) / ("f" * 32)
    orphan.mkdir(parents=True)
    (orphan / "media.mp4.part").write_bytes(b"x")
    manager.restore()
    assert not orphan.exists()
