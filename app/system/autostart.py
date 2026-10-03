"""Start with the system, per user and without administrator rights: the Run key on Windows, a LaunchAgent on macOS."""

from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

from app import APP_ID
from app.infrastructure.logger import get_logger
from app.utils.paths import is_fresh_profile, is_frozen

log = get_logger("autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_FLAG = "--autostart"


def _normalized(command: str) -> str:
    """The LaunchAgent stores an argument list; compare in the same form it is read back."""
    return shlex.join(shlex.split(command)) if sys.platform == "darwin" else command


def launch_command() -> str:
    if is_frozen():
        return f'"{sys.executable}" {AUTOSTART_FLAG}'
    python = Path(sys.executable)
    windowed = python.with_name("pythonw.exe")
    interpreter = windowed if windowed.is_file() else python
    script = Path(__file__).resolve().parents[2] / "run.py"
    return f'"{interpreter}" "{script}" {AUTOSTART_FLAG}'


class RegistryBackend:
    def read(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, APP_ID)
                return str(value)
        except OSError:
            return None

    def write(self, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_ID)
        except FileNotFoundError:
            pass


class LaunchAgentBackend:
    """~/Library/LaunchAgents/<label>.plist, loaded by launchd at the next login (RunAtLoad)."""

    LABEL = f"com.luut.{APP_ID}"

    @property
    def path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.LABEL}.plist"

    def read(self) -> str | None:
        try:
            with self.path.open("rb") as handle:
                data = plistlib.load(handle)
            return shlex.join(str(arg) for arg in data["ProgramArguments"])
        except (OSError, ValueError, KeyError, TypeError, plistlib.InvalidFileException):
            return None

    def write(self, command: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"Label": self.LABEL, "ProgramArguments": shlex.split(command), "RunAtLoad": True,
                "ProcessType": "Interactive"}
        with self.path.open("wb") as handle:
            plistlib.dump(data, handle)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def default_backend() -> RegistryBackend | LaunchAgentBackend:
    return LaunchAgentBackend() if sys.platform == "darwin" else RegistryBackend()


class AutoStart:
    def __init__(self, backend: RegistryBackend | LaunchAgentBackend | None = None) -> None:
        self._backend = backend or default_backend()

    @property
    def supported(self) -> bool:
        # The isolated --fresh-start profile must never register itself to start with the system.
        return sys.platform in ("win32", "darwin") and not is_fresh_profile()

    def is_enabled(self) -> bool:
        return self.supported and self._backend.read() is not None

    def apply(self, enabled: bool) -> bool:
        """Create/remove the Run entry. Returns False when it could not be changed."""
        if not self.supported:
            return False
        try:
            if enabled:
                command = launch_command()
                if self._backend.read() != _normalized(command):
                    self._backend.write(command)
                    log.info("Start with the system enabled")
            elif self._backend.read() is not None:
                self._backend.delete()
                log.info("Start with the system disabled")
        except OSError as exc:
            log.warning("Could not change start with the system: %s", exc)
            return False
        return True
