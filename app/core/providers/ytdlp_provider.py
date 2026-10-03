"""DownloadProvider backed by the official yt-dlp.exe (+ ffmpeg for merging audio/video streams).

yt-dlp is an external, independently updatable program (see `YtDlpManager`): it is run as a child process with a
fixed argument list (never a shell), and always from the single path the manager provides — so the desktop window and
the widget use the same installation, and a freshly installed version is used by the very next download.
Only public, technically supported extraction is used: no cookies, no credentials, no DRM handling.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.errors import AppError, ErrorKind, classify_os_error
from app.core.models.download import DownloadProgress
from app.core.models.video import (
    BEST_QUALITY, CONTAINER_AUDIO, CONTAINER_AUDIO_M4A, CONTAINER_AUDIO_ORIGINAL, CONTAINER_MP4, CONTAINER_ORIGINAL,
    CONTAINER_VIDEO_MP4, CONTAINER_VIDEO_ORIGINAL, QualityOption, VideoInfo,
)
from app.core.providers.base import (
    DownloadJob, DownloadProvider, DownloadStopped, EngineInfo, ProgressCallback, Stage,
    StageCallback, StopReason, StopToken,
)
from app.core.services.ytdlp_manager import YtDlpManager
from app.infrastructure.filesystem import PARTIAL_SUFFIXES
from app.infrastructure.logger import get_logger, mask_urls
from app.infrastructure.processes import ManagedProcess
from app.infrastructure.tools import find_ffmpeg
from app.utils.urls import normalize_url

log = get_logger("engine")
MIN_LISTED_HEIGHT = 360
AUDIO_CODEC = "mp3"
AUDIO_BITRATE_KBPS = 192
PROGRESS_INTERVAL = 0.25
ANALYSIS_TIMEOUT = 180
_MARK_PROGRESS, _MARK_STAGE, _MARK_FILE, _MARK_REQUESTED = "LUUTPROG ", "LUUTPP ", "LUUTFILE ", "LUUTREQ "

_ERROR_PATTERNS: list[tuple[ErrorKind, tuple[str, ...]]] = [
    (ErrorKind.DISK_FULL, ("no space left", "errno 28", "not enough space")),
    (ErrorKind.PERMISSION, ("permission denied", "errno 13", "access is denied")),
    (ErrorKind.DRM, ("drm",)),
    (ErrorKind.UNSUPPORTED, ("unsupported url", "no video formats found", "is not a valid url")),
    (ErrorKind.UNAVAILABLE, (
        "private video", "video unavailable", "not available", "sign in", "log in", "login",
        "members-only", "members only", "http error 401", "http error 403", "http error 404",
        "http error 410", "geo", "copyright", "has been removed", "requested format",
        "this live event", "premieres in", "age-restricted", "confirm your age", "paywall",
        "subscription")),
    (ErrorKind.NETWORK, (
        "timed out", "timeout", "urlopen error", "connection", "network", "getaddrinfo",
        "name resolution", "temporary failure", "errno 11001", "errno 11004", "reset by peer",
        "http error 5", "remote end closed", "incompleteread", "ssl", "unreachable",
        "unable to download")),
]


def classify_engine_error(exc: BaseException | str) -> AppError:
    """Map an error (exception or yt-dlp "ERROR: ..." text) to a friendly category."""
    if isinstance(exc, OSError):
        return classify_os_error(exc)
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    message = text.lower()
    for kind, needles in _ERROR_PATTERNS:
        if any(needle in message for needle in needles):
            return AppError(kind, text)
    return AppError(ErrorKind.UNEXPECTED, text)


def _size(fmt: dict[str, Any]) -> int | None:
    value = fmt.get("filesize") or fmt.get("filesize_approx")
    return int(value) if value else None


def _has_video(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") not in (None, "none") or (fmt.get("height") or 0) > 0


def _has_audio(fmt: dict[str, Any]) -> bool:
    return fmt.get("acodec") not in (None, "none")


def _usable(fmt: dict[str, Any]) -> bool:
    return not fmt.get("has_drm") and fmt.get("protocol") != "mhtml" and fmt.get("ext") != "mhtml"


class FormatCatalog:
    """Derives quality/container options from a yt-dlp info dict without inventing anything."""

    def __init__(self, info: dict[str, Any], has_ffmpeg: bool) -> None:
        self.has_ffmpeg = has_ffmpeg
        formats = [f for f in info.get("formats") or [info] if _usable(f)]
        self.video = [f for f in formats if _has_video(f) and f.get("height")]
        if not has_ffmpeg:
            self.video = [f for f in self.video if _has_audio(f) or f.get("acodec") is None]
        self.audio = [f for f in formats if _has_audio(f) and not _has_video(f)]
        # Streams with video and no audio (DASH): the only way to get "video without audio" honestly.
        self.video_only = [f for f in formats if _has_video(f) and f.get("height") and f.get("acodec") == "none"]
        self.all = formats

    def heights(self) -> list[int]:
        heights = sorted({int(f["height"]) for f in self.video}, reverse=True)
        listed = [h for h in heights if h >= MIN_LISTED_HEIGHT]
        return listed or heights

    def _best(self, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        return max(candidates, key=lambda f: (f.get("tbr") or 0, _size(f) or 0), default=None)

    def estimate(self, height: int) -> int | None:
        video = self._best([f for f in self.video if f.get("height") == height])
        if video is None:
            return None
        size = _size(video)
        if size is None:
            return None
        if not _has_audio(video) and self.has_ffmpeg:
            audio = self._best(self.audio)
            if audio is not None:
                audio_size = _size(audio)
                if audio_size is None:
                    return None
                size += audio_size
        return size

    def qualities(self) -> list[QualityOption]:
        heights = self.heights()
        options = [QualityOption(BEST_QUALITY, self.estimate(heights[0]) if heights else None)]
        options += [QualityOption(h, self.estimate(h)) for h in heights]
        return options

    def source_extensions(self) -> list[str]:
        exts = {str(f.get("ext")).lower() for f in (self.video or self.all) if f.get("ext")}
        return sorted(exts)

    def containers(self) -> list[str]:
        exts = self.source_extensions()
        if not self.video:
            options = [CONTAINER_ORIGINAL]
        else:
            options = [CONTAINER_MP4] if self.has_ffmpeg or "mp4" in exts else []
            if any(ext != "mp4" for ext in exts) or not options:
                options.append(CONTAINER_ORIGINAL)
        if self.has_ffmpeg or self.audio:
            options.append(CONTAINER_AUDIO)
        return options

    def audio_containers(self) -> list[str]:
        """Audio options besides CONTAINER_AUDIO. With ffmpeg, CONTAINER_AUDIO is a conversion to MP3, so M4A and the
        untouched original stream are offered too; without ffmpeg CONTAINER_AUDIO already is the original stream."""
        if not self.has_ffmpeg or not self.audio:
            return []
        return [CONTAINER_AUDIO_M4A, CONTAINER_AUDIO_ORIGINAL]

    def audio_original_format(self) -> str | None:
        best = self._best(self.audio)
        return str(best["ext"]).upper() if best and best.get("ext") else None

    def video_only_containers(self) -> list[str]:
        if not self.video_only:
            return []
        exts = {str(f.get("ext")).lower() for f in self.video_only if f.get("ext")}
        options = [CONTAINER_VIDEO_MP4] if self.has_ffmpeg or "mp4" in exts else []
        if any(ext != "mp4" for ext in exts) or not options:
            options.append(CONTAINER_VIDEO_ORIGINAL)
        return options

    def video_only_extensions(self) -> list[str]:
        return sorted({str(f.get("ext")).lower() for f in self.video_only if f.get("ext")})

    def video_only_qualities(self) -> list[QualityOption]:
        if not self.video_only:
            return []
        heights = sorted({int(f["height"]) for f in self.video_only}, reverse=True)
        heights = [h for h in heights if h >= MIN_LISTED_HEIGHT] or heights

        def size(height: int) -> int | None:
            best = self._best([f for f in self.video_only if f.get("height") == height])
            return _size(best) if best else None

        return [QualityOption(BEST_QUALITY, size(heights[0]))] + [QualityOption(h, size(h)) for h in heights]

    def audio_format(self) -> str | None:
        """Extension of the audio-only result: converted to MP3 with ffmpeg, else the best audio stream."""
        if self.has_ffmpeg:
            return AUDIO_CODEC.upper()
        best = self._best([f for f in self.audio if f.get("ext") == "m4a"] or self.audio)
        return str(best["ext"]).upper() if best and best.get("ext") else None

    def audio_estimate(self, duration: float | None) -> int | None:
        if self.has_ffmpeg:
            return int(duration * AUDIO_BITRATE_KBPS * 1000 / 8) if duration else None
        best = self._best([f for f in self.audio if f.get("ext") == "m4a"] or self.audio)
        return _size(best) if best else None


def _pick_thumbnail(info: dict[str, Any]) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        def score(t: dict[str, Any]) -> tuple[int, int, int]:
            width = t.get("width") or 0
            url = str(t["url"]).lower()
            jpeg = 1 if (".jpg" in url or ".jpeg" in url or ".png" in url) else 0
            fits = 1 if 0 < width <= 1280 else 0
            return fits, jpeg, width if fits else -width
        best = max(thumbs, key=score)
        return str(best["url"])
    thumb = info.get("thumbnail")
    return str(thumb) if thumb else None


def build_format_options(job: DownloadJob, has_ffmpeg: bool) -> dict[str, Any]:
    height = f"[height<=?{job.quality_height}]" if job.quality_height else ""
    opts: dict[str, Any] = {}
    if job.container == CONTAINER_AUDIO_M4A:
        opts["format"] = "ba[ext=m4a]/ba"
        if has_ffmpeg:  # copies AAC streams, converts anything else
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
        return opts
    if job.container == CONTAINER_AUDIO_ORIGINAL:
        opts["format"] = "ba"
        return opts
    if job.container in (CONTAINER_VIDEO_MP4, CONTAINER_VIDEO_ORIGINAL):
        if job.container == CONTAINER_VIDEO_MP4:
            if has_ffmpeg:
                opts["format"] = f"bv{height}"
                opts["format_sort"] = ["res", "vcodec:h264", "ext:mp4"]
                opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
            else:
                opts["format"] = f"bv{height}[ext=mp4]"
        else:
            opts["format"] = f"bv{height}"
        return opts
    if job.container == CONTAINER_AUDIO:
        if has_ffmpeg:
            opts["format"] = "ba/b"
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_CODEC,
                                       "preferredquality": str(AUDIO_BITRATE_KBPS)}]
        else:
            opts["format"] = "ba[ext=m4a]/ba"
        return opts
    if has_ffmpeg:
        opts["format"] = f"bv*{height}+ba/b{height}"
        if job.container == CONTAINER_MP4:
            opts["format_sort"] = ["res", "vcodec:h264", "ext:mp4:m4a"]
            opts["merge_output_format"] = "mp4"
            opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
    elif job.container == CONTAINER_MP4:
        opts["format"] = f"b{height}[ext=mp4]/b{height}"
    else:
        opts["format"] = f"b{height}"
    return opts


class _ProgressTracker:
    """Aggregates per-stream progress (video + audio) into one real overall progress."""

    def __init__(self, callback: ProgressCallback) -> None:
        self._callback = callback
        self._streams: dict[str, tuple[int, int | None]] = {}
        self._expected_streams = 1
        self._expected_total: int | None = None
        self._last_emit = 0.0

    def expect(self, requested_json: str) -> None:
        """Sizes of the streams yt-dlp is about to download (video + audio), so the total is right from the start."""
        try:
            requested = json.loads(requested_json)
        except ValueError:
            return
        if isinstance(requested, list) and requested and self._expected_total is None:
            self._expected_streams = len(requested)
            sizes = [(f.get("filesize") or f.get("filesize_approx")) if isinstance(f, dict) else None
                     for f in requested]
            if all(sizes):
                self._expected_total = int(sum(sizes))

    def update(self, data: dict[str, Any]) -> None:
        info = data.get("info_dict") or {}
        requested = info.get("requested_formats")
        if requested and self._expected_total is None:
            self._expected_streams = len(requested)
            sizes = [_size(f) for f in requested]
            if all(sizes):
                self._expected_total = sum(s for s in sizes if s)
        key = str(data.get("filename") or data.get("tmpfilename") or "stream")
        downloaded = int(data.get("downloaded_bytes") or 0)
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        total = int(total) if total else None
        finished = data.get("status") == "finished"
        if finished:
            total = total or downloaded
            downloaded = total
        self._streams[key] = (downloaded, total)

        now = time.monotonic()
        if not finished and now - self._last_emit < PROGRESS_INTERVAL:
            return
        self._last_emit = now
        self._callback(self._snapshot(data.get("speed"), data.get("eta")))

    def _snapshot(self, speed: float | None, eta: float | None) -> DownloadProgress:
        downloaded = sum(d for d, _ in self._streams.values())
        totals = [t for _, t in self._streams.values()]
        total: int | None = None
        if all(totals) and len(self._streams) >= self._expected_streams:
            total = sum(t for t in totals if t)
        if self._expected_total:
            total = max(self._expected_total, total or 0)
        if total is not None and downloaded > total:
            total = downloaded
        if total and speed:
            eta = (total - downloaded) / speed
        elif total is None and self._expected_streams > 1:
            eta = None
        return DownloadProgress(downloaded, total, speed, eta)


def cli_format_args(job: DownloadJob, has_ffmpeg: bool) -> list[str]:
    """The same choices as `build_format_options`, as yt-dlp command-line arguments."""
    opts = build_format_options(job, has_ffmpeg)
    args = ["-f", str(opts["format"])]
    if opts.get("format_sort"):
        args += ["-S", ",".join(opts["format_sort"])]
    if opts.get("merge_output_format"):
        args += ["--merge-output-format", str(opts["merge_output_format"])]
    for processor in opts.get("postprocessors", []):
        if processor["key"] == "FFmpegVideoRemuxer":
            args += ["--remux-video", str(processor["preferedformat"])]
        elif processor["key"] == "FFmpegExtractAudio":
            args += ["-x", "--audio-format", str(processor["preferredcodec"])]
            if processor.get("preferredquality"):
                args += ["--audio-quality", f"{processor['preferredquality']}K"]
    return args


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # yt-dlp prints "NA" for unknown values


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """`LUUTPROG status|downloaded|total|estimate|speed|eta|filename` -> progress-hook-like dict."""
    parts = line[len(_MARK_PROGRESS):].split("|", 6)
    if len(parts) != 7:
        return None
    status, downloaded, total, estimate, speed, eta, filename = parts
    number = _number(downloaded)
    return {"status": status, "downloaded_bytes": int(number) if number is not None else 0,
            "total_bytes": _number(total), "total_bytes_estimate": _number(estimate), "speed": _number(speed),
            "eta": _number(eta), "filename": filename.strip()}


def _reader(stream, name: str, lines: queue.Queue) -> None:
    try:
        for raw in iter(stream.readline, b""):
            lines.put((name, raw.decode("utf-8", errors="replace").rstrip("\r\n")))
    except (OSError, ValueError):  # the pipe was closed because the process was stopped
        pass
    finally:
        lines.put((name, None))


class YtDlpProvider(DownloadProvider):
    def __init__(self, manager: YtDlpManager | None = None, ffmpeg_path: str | None = None,
                 autodetect_ffmpeg: bool = True) -> None:
        self._manager = manager
        self._ffmpeg = ffmpeg_path or (find_ffmpeg() if autodetect_ffmpeg else None)

    @property
    def manager(self) -> YtDlpManager:
        if self._manager is None:
            from app.core.services.ytdlp_manager import default_manager

            self._manager = default_manager()
        return self._manager

    @property
    def engine(self) -> EngineInfo:
        return EngineInfo("yt-dlp", self.manager.get_installed_version() or "não instalado")

    @property
    def supports_resume(self) -> bool:
        return True

    @property
    def has_ffmpeg(self) -> bool:
        return bool(self._ffmpeg)

    def _base_args(self) -> list[str]:
        args = ["--ignore-config", "--no-playlist", "--color", "never", "--encoding", "utf-8",
                "--socket-timeout", "20", "--retries", "5", "--fragment-retries", "10", "--extractor-retries", "2"]
        if self._ffmpeg:
            args += ["--ffmpeg-location", self._ffmpeg]
        return args

    @contextmanager
    def _lease(self) -> Iterator[Path]:
        """The executable, protected from being replaced by an update while this process runs."""
        lease = self.manager.lease()
        try:
            exe = lease.__enter__()
        except FileNotFoundError as exc:
            raise AppError(ErrorKind.ENGINE_MISSING, "yt-dlp executable not found") from exc
        try:
            yield exe
        finally:
            lease.__exit__(None, None, None)

    @staticmethod
    def _log_stderr(lines: list[str]) -> str | None:
        """Log yt-dlp's messages (URLs masked) and return the last ERROR line."""
        error = None
        for line in lines:
            if line.startswith("ERROR:"):
                error = line[6:].strip()
                log.warning("engine error: %s", mask_urls(line))
            elif line.startswith("WARNING:"):
                log.info("engine warning: %s", mask_urls(line))
            elif line.strip():
                log.debug("engine: %s", mask_urls(line))
        return error

    # ---------------------------------------------------------------- analysis
    def analyze(self, url: str) -> VideoInfo:
        normalized = normalize_url(url)
        if normalized is None:
            raise AppError(ErrorKind.INVALID_URL, "Rejected before analysis")
        with self._lease() as exe:
            args = [str(exe), *self._base_args(), "-J", "--flat-playlist", "--", normalized]
            try:
                with ManagedProcess(args) as process:
                    try:
                        stdout, stderr = process.popen.communicate(timeout=ANALYSIS_TIMEOUT)
                    except subprocess.TimeoutExpired as exc:
                        process.kill()
                        raise AppError(ErrorKind.NETWORK, "Analysis timed out") from exc
            except OSError as exc:
                raise classify_engine_error(exc) from exc
        error = self._log_stderr(stderr.decode("utf-8", errors="replace").splitlines())
        if process.popen.returncode != 0:
            raise classify_engine_error(error or f"yt-dlp exit code {process.popen.returncode}")
        try:
            info = json.loads(stdout.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise AppError(ErrorKind.UNEXPECTED, "yt-dlp returned invalid JSON") from exc
        if not isinstance(info, dict) or not info:
            raise AppError(ErrorKind.UNSUPPORTED, "Empty info")
        if info.get("_type") in ("playlist", "multi_video"):
            raise AppError(ErrorKind.PLAYLIST, "Playlist URL")
        if info.get("is_live") or info.get("live_status") == "is_live":
            raise AppError(ErrorKind.LIVE, "Live stream")
        return self._to_video_info(normalized, info)

    def _to_video_info(self, url: str, info: dict[str, Any]) -> VideoInfo:
        formats = info.get("formats") or []
        if formats and all(f.get("has_drm") for f in formats):
            raise AppError(ErrorKind.DRM, "All formats are DRM protected")
        catalog = FormatCatalog(info, self.has_ffmpeg)
        if not catalog.all:
            raise AppError(ErrorKind.UNAVAILABLE, "No usable formats")
        heights = catalog.heights()
        return VideoInfo(
            url=str(info.get("webpage_url") or url),
            title=info.get("title") or None,
            duration=info.get("duration"),
            uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
            thumbnail_url=_pick_thumbnail(info),
            extractor=info.get("extractor_key") or info.get("extractor"),
            max_height=heights[0] if heights else None,
            source_formats=catalog.source_extensions(),
            qualities=catalog.qualities(),
            containers=catalog.containers(),
            audio_size=catalog.audio_estimate(info.get("duration")),
            audio_format=catalog.audio_format(),
            audio_containers=catalog.audio_containers(),
            audio_original_format=catalog.audio_original_format(),
            video_only_containers=catalog.video_only_containers(),
            video_only_qualities=catalog.video_only_qualities(),
            video_only_formats=catalog.video_only_extensions(),
        )

    # ---------------------------------------------------------------- download
    def download_args(self, exe: Path, job: DownloadJob, work_dir: Path) -> list[str]:
        return [
            str(exe), *self._base_args(), *cli_format_args(job, self.has_ffmpeg),
            "-o", str(work_dir / "media.%(ext)s"), "--continue", "--no-overwrites", "--part",
            "--no-simulate", "--progress", "--newline",
            "--print", f"before_dl:{_MARK_REQUESTED}%(requested_formats.:.{{filesize,filesize_approx}})j",
            "--print", f"after_move:{_MARK_FILE}%(filepath)s",
            "--progress-template", f"download:{_MARK_PROGRESS}%(progress.status)s|%(progress.downloaded_bytes)s|"
                                   "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s|"
                                   "%(progress.eta)s|%(progress.filename)s",
            "--progress-template", f"postprocess:{_MARK_STAGE}%(progress.status)s|%(progress.postprocessor)s",
            "--", job.url,
        ]

    def download(self, job: DownloadJob, work_dir: Path, on_progress: ProgressCallback,
                 on_stage: StageCallback, token: StopToken) -> Path:
        work_dir.mkdir(parents=True, exist_ok=True)
        tracker = _ProgressTracker(on_progress)
        output: Path | None = None
        stderr: list[str] = []
        with self._lease() as exe:
            if token.is_set():
                raise DownloadStopped(token.reason or StopReason.CANCEL)
            on_stage(Stage.DOWNLOADING)
            try:
                process = ManagedProcess(self.download_args(exe, job, work_dir))
            except OSError as exc:
                raise classify_engine_error(exc) from exc
            with process:
                lines: queue.Queue = queue.Queue()
                for stream, name in ((process.popen.stdout, "out"), (process.popen.stderr, "err")):
                    threading.Thread(target=_reader, args=(stream, name, lines), daemon=True,
                                     name=f"yt-dlp-{name}").start()
                open_streams = 2
                while open_streams:
                    if token.is_set():
                        process.kill()  # partial .part data stays in work_dir for resuming
                        raise DownloadStopped(token.reason or StopReason.CANCEL)
                    try:
                        name, line = lines.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        open_streams -= 1
                        continue
                    if line.startswith(_MARK_PROGRESS):
                        data = parse_progress_line(line)
                        if data:
                            tracker.update(data)
                    elif line.startswith(_MARK_STAGE):
                        status, _, processor = line[len(_MARK_STAGE):].partition("|")
                        if status == "started" and processor.strip() not in ("MoveFiles", ""):
                            on_stage(Stage.PROCESSING)
                    elif line.startswith(_MARK_REQUESTED):
                        tracker.expect(line[len(_MARK_REQUESTED):])
                    elif line.startswith(_MARK_FILE):
                        output = Path(line[len(_MARK_FILE):].strip())
                    elif name == "err":
                        stderr.append(line)
                process.popen.wait()
                code = process.popen.returncode
        if token.is_set():
            raise DownloadStopped(token.reason or StopReason.CANCEL)
        error = self._log_stderr(stderr)
        if code != 0:
            raise classify_engine_error(error or f"yt-dlp exit code {code}")
        if output is not None and output.is_file():
            return output
        return self._output_file(work_dir)

    @staticmethod
    def _output_file(work_dir: Path) -> Path:
        candidates = [p for p in work_dir.iterdir()
                      if p.is_file() and p.stem == "media" and p.suffix not in PARTIAL_SUFFIXES]
        if not candidates:
            raise AppError(ErrorKind.UNEXPECTED, "Engine finished but no output file was found")
        return max(candidates, key=lambda p: p.stat().st_size)
