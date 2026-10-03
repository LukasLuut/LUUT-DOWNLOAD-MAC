"""Single instance: a second launch hands its request to the running instance and exits.

Only one DownloadManager, queue and database connection ever exist. The running instance listens on a per-user local
socket; commands are short fixed words (never paths or URLs).
"""

from __future__ import annotations

import getpass
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app import APP_ID
from app.infrastructure.logger import get_logger

log = get_logger("instance")

CMD_SHOW = "show"
CMD_WIDGET = "widget"
COMMANDS = frozenset({CMD_SHOW, CMD_WIDGET})


def server_name(suffix: str = "") -> str:
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = "user"
    return f"{APP_ID}-{user}{suffix}"


def notify_running_instance(name: str, command: str = CMD_SHOW, timeout_ms: int = 300) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(command.encode("ascii"))
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


def instance_running(name: str, timeout_ms: int = 300) -> bool:
    """True when the app is open (connects without sending a command, so nothing is shown)."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    running = socket.waitForConnected(timeout_ms)
    socket.abort()
    return running


def wait_for_previous_instance(name: str, timeout: float = 20.0) -> None:
    """Used after "Restaurar aplicativo": the old instance is still closing when we start."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(200):
            return
        socket.abort()
        time.sleep(0.25)


class InstanceServer(QObject):
    command_received = Signal(str)

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._sockets: set[QLocalSocket] = set()
        QLocalServer.removeServer(name)
        self.listening = self._server.listen(name)
        if not self.listening:
            log.warning("Single-instance server not listening: %s", self._server.errorString())
        self._server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._sockets.add(socket)
            socket.readyRead.connect(self._on_ready_read)
            socket.disconnected.connect(self._on_disconnected)
            if socket.bytesAvailable():
                self._read(socket)

    def _on_ready_read(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket):
            self._read(socket)

    def _on_disconnected(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket) and socket in self._sockets:
            self._read(socket)
            self._sockets.discard(socket)
            socket.deleteLater()

    def _read(self, socket: QLocalSocket) -> None:
        data = bytes(socket.readAll().data()).decode("ascii", errors="ignore").strip()
        if not data:
            return
        command = data if data in COMMANDS else CMD_SHOW
        log.info("Second launch detected: %s", command)
        self.command_received.emit(command)

    def close(self) -> None:
        for socket in list(self._sockets):
            socket.readyRead.disconnect(self._on_ready_read)
            socket.disconnected.disconnect(self._on_disconnected)
            socket.abort()
            socket.deleteLater()
        self._sockets.clear()
        self._server.close()
