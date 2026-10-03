"""Child processes that can always be stopped completely.

The official yt-dlp.exe is a one-file executable: it starts a second process that does the real work. Every child is
placed in a Windows Job Object configured with KILL_ON_JOB_CLOSE, so pausing/cancelling stops the whole tree and a
crash of Luut never leaves yt-dlp running in the background. On macOS each child starts its own process group, and
`kill()` signals the whole group.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes

from app.infrastructure.logger import get_logger

log = get_logger("processes")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_ALL_ACCESS = 0x1F0FFF


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount",
        "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class ManagedProcess:
    """A subprocess.Popen whose whole process tree is terminated by `kill()`."""

    def __init__(self, args: list[str]) -> None:
        self._job = None
        self.popen = subprocess.Popen(  # noqa: S603 - fixed executable path, arguments as a list, no shell
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW, start_new_session=sys.platform != "win32")
        if sys.platform == "win32":
            self._job = self._create_job()

    def _create_job(self):
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.OpenProcess.restype = wintypes.HANDLE
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(wintypes.HANDLE(job), _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                             ctypes.byref(info), ctypes.sizeof(info))
            handle = kernel32.OpenProcess(_PROCESS_ALL_ACCESS, False, self.popen.pid)
            if handle:
                kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle))
                kernel32.CloseHandle(wintypes.HANDLE(handle))
            return job
        except (OSError, AttributeError) as exc:
            log.debug("Job object unavailable: %s", exc)
            return None

    def kill(self) -> None:
        if self._job:
            ctypes.windll.kernel32.TerminateJobObject(wintypes.HANDLE(self._job), 1)
        if self.popen.poll() is None:
            if sys.platform == "win32":  # fallback: terminate the tree even without a job object
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.popen.pid)],  # noqa: S603, S607
                               capture_output=True, creationflags=_NO_WINDOW, check=False)
            else:  # the child leads its own process group (start_new_session): stop it and everything it started
                try:
                    os.killpg(self.popen.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self.popen.kill()
        try:
            self.popen.wait(10)
        except subprocess.TimeoutExpired:
            log.warning("Child process %s did not exit after kill", self.popen.pid)

    def close(self) -> None:
        if self._job:
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._job))
            self._job = None

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.popen.poll() is None:
            self.kill()
        for stream in (self.popen.stdout, self.popen.stderr):
            if stream:
                stream.close()
        self.close()
