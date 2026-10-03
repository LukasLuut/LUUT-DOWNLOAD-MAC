"""yt-dlp as an external, independently updatable dependency (Qt-free).

The official standalone yt-dlp (`yt-dlp.exe` on Windows, `yt-dlp_macos` on macOS) lives outside the Luut executable,
in a folder the user can always write to:

    Windows: %LOCALAPPDATA%\\LuutVideoDownloader\\bin\\yt-dlp.exe      (managed copy: installed, updated, rolled back)
    macOS:   ~/Library/Application Support/LuutVideoDownloader/bin/yt-dlp_macos
    <program folder or .app resources>/bin/<same name>                (optional seed shipped with the build)

Updates come only from the official GitHub releases of yt-dlp/yt-dlp and are verified against the release's
SHA2-256SUMS before anything is executed. A new file is downloaded next to the current one, validated, and only then
swapped in (the previous version is kept as a backup until the new one answers `--version`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.infrastructure.logger import get_logger

log = get_logger("ytdlp")

IS_MAC = sys.platform == "darwin"
# Official release asset for this platform. yt-dlp_macos is a universal2 binary (Intel + Apple Silicon).
EXE_NAME = "yt-dlp_macos" if IS_MAC else "yt-dlp.exe"
NEW_NAME = "yt-dlp_macos.new" if IS_MAC else "yt-dlp.new.exe"
# First bytes of a valid executable: Mach-O (universal / 64-bit) on macOS, PE ("MZ") on Windows.
EXE_MAGIC = (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe") if IS_MAC else (b"MZ",)
PLATFORM_NAME = "macOS" if IS_MAC else "Windows"
REPOSITORY = "yt-dlp/yt-dlp"
API_LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{{tag}}"
WEB_LATEST = f"https://github.com/{REPOSITORY}/releases/latest"
DOWNLOAD_BASE = f"https://github.com/{REPOSITORY}/releases/download/"
CHECKSUMS_NAME = "SHA2-256SUMS"
# Final hosts GitHub redirects release downloads to.
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                 "api.github.com")
MIN_SIZE, MAX_SIZE = 5 * 1024 * 1024, 300 * 1024 * 1024
HTTP_TIMEOUT = 20
VERSION_TIMEOUT = 90  # first run of a one-file exe unpacks itself (and antivirus scans it): be patient
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)*$")


# ------------------------------------------------------------------------------ versions
def parse_version(text: str | None) -> tuple[int, ...] | None:
    """"2026.09.20" -> (2026, 9, 20); nightly "2026.09.20.123456" -> 4 parts. None when not a yt-dlp version."""
    value = (text or "").strip().lstrip("v")
    if not _VERSION_RE.match(value):
        return None
    return tuple(int(part) for part in value.split("."))


def compare_versions(a: str, b: str) -> int:
    """-1 / 0 / 1, comparing numerically (2026.9 < 2026.10)."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        raise ValueError(f"Invalid version: {a!r} / {b!r}")
    width = max(len(pa), len(pb))
    pa, pb = pa + (0,) * (width - len(pa)), pb + (0,) * (width - len(pb))
    return (pa > pb) - (pa < pb)


class UpdateStatus(str, Enum):
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    NOT_INSTALLED = "not_installed"
    CHECK_FAILED = "check_failed"


class YtDlpError(Exception):
    """`message` is friendly (pt-BR); the technical detail goes to the logs."""

    def __init__(self, message: str, detail: str = "", rolled_back: bool = False) -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail
        self.rolled_back = rolled_back


class UpdateCancelled(YtDlpError):
    def __init__(self) -> None:
        super().__init__("Atualização cancelada. A versão atual do yt-dlp foi mantida.")


OFFLINE_MESSAGE = ("Não foi possível verificar atualizações. Verifique sua conexão com a internet. O Luut continuará "
                   "funcionando com a versão atual do yt-dlp.")


@dataclass(frozen=True)
class Release:
    version: str
    exe_url: str
    checksums_url: str
    size: int | None = None


@dataclass(frozen=True)
class UpdateCheck:
    status: UpdateStatus
    installed: str | None
    latest: str | None
    error: str | None = None
    release: Release | None = None


# ------------------------------------------------------------------------------ network
def _allowed(url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not _allowed(newurl):
            raise urllib.error.URLError(f"Redirect to an unexpected host refused: {newurl.split('?')[0]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects())


def _request(url: str, accept: str = "*/*") -> urllib.request.Request:
    if not _allowed(url):
        raise YtDlpError("A fonte da atualização não é oficial.", f"Refused URL: {url}")
    return urllib.request.Request(url, headers={"User-Agent": "LuutVideoDownloader", "Accept": accept})


class GitHubReleases:
    """Official distribution: https://github.com/yt-dlp/yt-dlp/releases."""

    def __init__(self, timeout: float = HTTP_TIMEOUT) -> None:
        self.timeout = timeout

    def latest(self) -> Release:
        try:
            return self._from_api(API_LATEST)
        except YtDlpError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:  # rate limit, API change, flaky network…
            log.info("GitHub API unavailable (%s); resolving the latest release page", type(exc).__name__)
            return self._from_redirect()

    def release(self, version: str) -> Release:
        """A specific official release (used to repair/downgrade and by the build)."""
        if parse_version(version) is None:
            raise YtDlpError("Versão inválida.", f"Invalid version {version!r}")
        try:
            return self._from_api(API_TAG.format(tag=version))
        except (OSError, ValueError, KeyError, TypeError):
            return self._build(version, None)

    def _from_api(self, url: str) -> Release:
        with _opener.open(_request(url, "application/vnd.github+json"), timeout=self.timeout) as response:
            data = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        tag = str(data["tag_name"])
        assets = {a["name"]: a for a in data.get("assets") or [] if isinstance(a, dict) and a.get("name")}
        if EXE_NAME not in assets or CHECKSUMS_NAME not in assets:
            raise YtDlpError(f"A versão mais recente do yt-dlp não possui o executável para {PLATFORM_NAME}.",
                             f"Release {tag} assets: {sorted(assets)[:20]}")
        exe = assets[EXE_NAME]
        release = self._build(tag, int(exe["size"]) if exe.get("size") else None)
        for url_ in (str(exe.get("browser_download_url") or ""), str(assets[CHECKSUMS_NAME].get(
                "browser_download_url") or "")):
            if not url_.startswith(DOWNLOAD_BASE):
                raise YtDlpError("A fonte da atualização não é oficial.", f"Unexpected asset URL: {url_}")
        return release

    def _from_redirect(self) -> Release:
        request = _request(WEB_LATEST)
        request.method = "HEAD"
        with _opener.open(request, timeout=self.timeout) as response:
            final = response.geturl()
        tag = final.rstrip("/").rsplit("/", 1)[-1]
        if "/releases/tag/" not in final or parse_version(tag) is None:
            raise YtDlpError(OFFLINE_MESSAGE, f"Unexpected latest release URL: {final}")
        return self._build(tag, None)

    @staticmethod
    def _build(tag: str, size: int | None) -> Release:
        if parse_version(tag) is None:
            raise YtDlpError("A fonte oficial retornou uma versão inválida.", f"Invalid tag {tag!r}")
        return Release(tag, f"{DOWNLOAD_BASE}{tag}/{EXE_NAME}", f"{DOWNLOAD_BASE}{tag}/{CHECKSUMS_NAME}", size)

    def fetch_checksum(self, release: Release) -> str:
        with _opener.open(_request(release.checksums_url), timeout=self.timeout) as response:
            text = response.read(256 * 1024).decode("utf-8", errors="replace")
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[1].lstrip("*") == EXE_NAME and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
        raise YtDlpError("A atualização não pôde ser validada. A versão atual do yt-dlp foi mantida.",
                         f"{EXE_NAME} not listed in SHA2-256SUMS")

    def download(self, release: Release, target: Path, progress: Callable[[int, int | None], None],
                 cancel: threading.Event) -> None:
        with _opener.open(_request(release.exe_url), timeout=self.timeout) as response:
            total = int(response.headers.get("Content-Length") or 0) or release.size
            if total and total > MAX_SIZE:
                raise YtDlpError("O arquivo de atualização tem um tamanho inesperado.", f"Content-Length {total}")
            done = 0
            with target.open("wb") as handle:
                while True:
                    if cancel.is_set():
                        raise UpdateCancelled()
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if done > MAX_SIZE:
                        raise YtDlpError("O arquivo de atualização tem um tamanho inesperado.", f"> {MAX_SIZE} bytes")
                    progress(done, total)
        if total and done != total:
            raise YtDlpError("O download da atualização foi interrompido. A versão atual do yt-dlp foi mantida.",
                             f"Incomplete download: {done}/{total}")


# ------------------------------------------------------------------------------ process
Runner = Callable[[list[str], float], subprocess.CompletedProcess]


def run_process(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW,  # noqa: S603
                          stdin=subprocess.DEVNULL, check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ------------------------------------------------------------------------------ manager
Progress = Callable[[str, int, int | None], None]  # phase ("download"/"validate"/"install"), done, total


class YtDlpManager:
    """Locates, versions, installs, updates and rolls back the yt-dlp executable. Thread-safe."""

    def __init__(self, install_dir: Path, seed_dirs: tuple[Path, ...] = (), source: GitHubReleases | None = None,
                 runner: Runner = run_process) -> None:
        self.install_dir = install_dir
        self._seeds = tuple(seed_dirs)
        self.source = source or GitHubReleases()
        self._run = runner
        self._cond = threading.Condition()
        self._leases = 0
        self._installing = False
        self._version_cache: tuple[str, float, int, str | None] | None = None  # path, mtime, size, version

    # -------------------------------------------------------------- location
    @property
    def managed_path(self) -> Path:
        return self.install_dir / EXE_NAME

    def get_executable_path(self) -> Path | None:
        """The one path every download/analysis uses. Only app-defined folders are considered (never the PATH)."""
        managed = self.managed_path
        if managed.is_file():
            return managed
        for folder in self._seeds:
            seed = folder / EXE_NAME
            if seed.is_file() and seed.resolve() != managed.resolve():
                try:  # adopt the copy shipped with the build so it can be updated in a writable folder
                    self.install_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(seed, managed)
                    _make_executable(managed)
                    log.info("yt-dlp copied from the application folder to %s", managed)
                    return managed
                except OSError as exc:
                    log.warning("Could not copy bundled yt-dlp (%s); using it in place", exc)
                    return seed
        return None

    def is_installed(self) -> bool:
        return self.get_executable_path() is not None

    # --------------------------------------------------------------- version
    def _version_of(self, exe: Path) -> str | None:
        try:
            result = self._run([str(exe), "--version"], VERSION_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("yt-dlp --version failed for %s: %s", exe.name, exc)
            return None
        text = (result.stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
        version = text[-1].strip() if text else ""
        if result.returncode != 0 or parse_version(version) is None:
            log.error("yt-dlp --version returned code %s and %r", result.returncode, version[:80])
            return None
        return version

    def get_installed_version(self, refresh: bool = False) -> str | None:
        """Always confirmed by running `yt-dlp --version` (cached while the file itself does not change)."""
        exe = self.get_executable_path()
        if exe is None:
            return None
        try:
            stat = exe.stat()
        except OSError:
            return None
        key = (str(exe), stat.st_mtime, stat.st_size)
        cache = self._version_cache
        if not refresh and cache and cache[:3] == key:
            return cache[3]
        version = self._version_of(exe)
        self._version_cache = (*key, version)
        if version:
            log.info("yt-dlp version detected: %s", version)
        return version

    # ----------------------------------------------------------------- check
    def check_for_updates(self) -> UpdateCheck:
        installed = self.get_installed_version() if self.is_installed() else None
        log.info("Checking yt-dlp updates")
        try:
            release = self.source.latest()
        except YtDlpError as error:
            log.warning("yt-dlp update check failed: %s", error.detail or error.message)
            return UpdateCheck(UpdateStatus.CHECK_FAILED, installed, None, error.message)
        except (OSError, ValueError) as exc:
            log.warning("yt-dlp update check failed: %s", exc)
            return UpdateCheck(UpdateStatus.CHECK_FAILED, installed, None, OFFLINE_MESSAGE)
        log.info("Latest yt-dlp version: %s", release.version)
        if installed is None:
            status = UpdateStatus.NOT_INSTALLED
        elif compare_versions(release.version, installed) > 0:
            status = UpdateStatus.UPDATE_AVAILABLE
            log.info("yt-dlp update available: %s -> %s", installed, release.version)
        else:
            status = UpdateStatus.UP_TO_DATE
        return UpdateCheck(status, installed, release.version, release=release)

    # ------------------------------------------------------------------ usage
    @contextmanager
    def lease(self) -> Iterator[Path]:
        """Held while a yt-dlp process runs, so the executable is never replaced under it."""
        with self._cond:
            while self._installing:
                self._cond.wait()
            path = self.get_executable_path()
            if path is None:
                raise FileNotFoundError(EXE_NAME)
            self._leases += 1
        try:
            yield path
        finally:
            with self._cond:
                self._leases -= 1
                self._cond.notify_all()

    @property
    def in_use(self) -> bool:
        return self._leases > 0

    @property
    def installing(self) -> bool:
        return self._installing

    # ---------------------------------------------------------------- install
    def install(self, release: Release | None = None, progress: Progress | None = None,
                cancel: threading.Event | None = None, wait_idle: float = 30.0) -> str:
        """Download, verify and install `release` (latest by default). Returns the installed version.

        The current yt-dlp keeps working if anything fails: the new file is only swapped in after it was verified,
        and the previous one is restored if the swapped-in file does not answer `--version`."""
        progress = progress or (lambda *_: None)
        cancel = cancel or threading.Event()
        release = release or self.source.latest()
        self.install_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.install_dir / NEW_NAME
        partial = self.install_dir / f"{EXE_NAME}.download"
        for leftover in (candidate, partial):
            with suppress(OSError):
                leftover.unlink()
        try:
            log.info("Downloading yt-dlp %s", release.version)
            self.source.download(release, partial, lambda done, total: progress("download", done, total), cancel)
            log.info("Download completed")
            progress("validate", 0, None)
            os.replace(partial, candidate)
            _make_executable(candidate)
            self._validate(candidate, release)
            if cancel.is_set():
                raise UpdateCancelled()
            progress("install", 0, None)
            return self._swap(candidate, release, wait_idle)
        except UpdateCancelled:
            log.info("yt-dlp update cancelled by the user")
            raise
        except YtDlpError as error:
            log.error("yt-dlp update failed: %s", error.detail or error.message)
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.error("Failed to download yt-dlp update: %s", exc)
            raise YtDlpError("Não foi possível baixar a atualização. A versão atual do yt-dlp foi mantida.",
                             str(exc)) from exc
        finally:
            for leftover in (candidate, partial):
                with suppress(OSError):
                    leftover.unlink()

    def _validate(self, candidate: Path, release: Release) -> None:
        log.info("Validating new executable")
        invalid = "A atualização não pôde ser validada. A versão atual do yt-dlp foi mantida."
        size = candidate.stat().st_size
        if not MIN_SIZE <= size <= MAX_SIZE:
            raise YtDlpError(invalid, f"Implausible size: {size}")
        with candidate.open("rb") as handle:
            header = handle.read(4)
            if not any(header.startswith(magic) for magic in EXE_MAGIC):
                raise YtDlpError(invalid, f"Not a {PLATFORM_NAME} executable")
        expected = self.source.fetch_checksum(release)
        actual = sha256(candidate)
        if actual != expected:
            raise YtDlpError(invalid, f"SHA-256 mismatch: {actual} != {expected}")
        version = self._version_of(candidate)  # only executed after the official checksum matched
        if version is None or compare_versions(version, release.version) != 0:
            raise YtDlpError(invalid, f"New executable reports {version!r}, expected {release.version}")

    def _swap(self, candidate: Path, release: Release, wait_idle: float) -> str:
        target = self.managed_path
        backup = self.install_dir / f"{EXE_NAME}.backup"
        with self._cond:
            deadline = time.monotonic() + wait_idle
            while self._leases:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise YtDlpError("O yt-dlp está em uso. Tente novamente quando os downloads terminarem.",
                                     f"{self._leases} active process(es)")
                self._cond.wait(remaining)
            self._installing = True  # new downloads wait the few seconds of the swap
        had_previous = target.is_file()
        backed_up = swapped = False
        try:
            if had_previous:
                with suppress(OSError):
                    backup.unlink()
                os.replace(target, backup)
                backed_up = True
                log.info("Backup created")
            log.info("Replacing yt-dlp executable")
            os.replace(candidate, target)
            swapped = True
            self._version_cache = None
            version = self._version_of(target)
            if version is None or compare_versions(version, release.version) != 0:
                raise YtDlpError("", f"Installed file reports {version!r}")
            log.info("New version validated")
            with suppress(OSError):
                backup.unlink()
            log.info("Update completed successfully: yt-dlp %s", version)
            return version
        except (OSError, YtDlpError) as exc:
            restored = self._rollback(target, backup, backed_up, swapped)
            detail = getattr(exc, "detail", "") or str(exc)
            if not had_previous:
                raise YtDlpError("A instalação do yt-dlp falhou. Tente novamente.", detail) from exc
            raise YtDlpError("A atualização falhou. A versão anterior do yt-dlp foi restaurada.", detail,
                             rolled_back=restored) from exc
        finally:
            with self._cond:
                self._installing = False
                self._cond.notify_all()

    def _rollback(self, target: Path, backup: Path, backed_up: bool, swapped: bool) -> bool:
        """Put the previous file back. Returns True when the previous version answers again."""
        self._version_cache = None
        if swapped:
            with suppress(OSError):
                target.unlink()
        if backed_up and backup.is_file() and not target.exists():
            os.replace(backup, target)
            log.warning("yt-dlp rollback: previous version restored")
        return target.is_file() and self._version_of(target) is not None

    def recover_interrupted_install(self) -> None:
        """A crash between backup and validation leaves `<exe>.backup` without `<exe>`: restore it."""
        backup = self.install_dir / f"{EXE_NAME}.backup"
        try:
            if backup.is_file() and not self.managed_path.is_file():
                os.replace(backup, self.managed_path)
                log.warning("Restored yt-dlp backup left by an interrupted update")
        except OSError as exc:
            log.error("Could not restore the yt-dlp backup: %s", exc)
        for leftover in (NEW_NAME, f"{EXE_NAME}.download"):
            with suppress(OSError):
                (self.install_dir / leftover).unlink()


def _make_executable(path: Path) -> None:
    """Downloaded/copied files have no execute bit on macOS (no-op on Windows)."""
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def default_manager() -> YtDlpManager:
    """The application's single manager: managed copy in the user profile, optional seed next to the program
    (Windows) or inside the .app bundle's resources (macOS)."""
    from app.utils.paths import app_install_dir, resource_path, tools_dir

    seeds = (app_install_dir() / "bin", resource_path("bin"))
    return YtDlpManager(tools_dir(), seed_dirs=tuple(dict.fromkeys(seeds)))
