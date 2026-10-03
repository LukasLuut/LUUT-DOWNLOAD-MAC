"""Metadata obtained from analysing a URL."""

from __future__ import annotations

from dataclasses import dataclass, field

BEST_QUALITY = 0

# A "container" key encodes what is downloaded and how it is saved. Keys are persisted with each task.
CONTAINER_MP4 = "mp4"                       # video + audio, MP4
CONTAINER_ORIGINAL = "original"             # video + audio, source format
CONTAINER_AUDIO = "audio"                   # audio only: MP3 (converted with ffmpeg) or the best original stream
CONTAINER_AUDIO_M4A = "audio_m4a"           # audio only, M4A (needs ffmpeg when the source has no M4A stream)
CONTAINER_AUDIO_ORIGINAL = "audio_original"  # audio only, best stream without conversion
CONTAINER_VIDEO_MP4 = "video_mp4"           # video without audio, MP4
CONTAINER_VIDEO_ORIGINAL = "video_original"  # video without audio, source format

KIND_AV = "av"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"
KIND_LABELS = {KIND_AV: "Vídeo + Áudio", KIND_VIDEO: "Vídeo somente", KIND_AUDIO: "Áudio somente"}

AUDIO_CONTAINERS = (CONTAINER_AUDIO, CONTAINER_AUDIO_M4A, CONTAINER_AUDIO_ORIGINAL)
VIDEO_ONLY_CONTAINERS = (CONTAINER_VIDEO_MP4, CONTAINER_VIDEO_ORIGINAL)

CONTAINER_LABELS = {
    CONTAINER_MP4: "MP4", CONTAINER_ORIGINAL: "Formato original", CONTAINER_AUDIO: "Somente áudio",
    CONTAINER_AUDIO_M4A: "Somente áudio (M4A)", CONTAINER_AUDIO_ORIGINAL: "Somente áudio (original)",
    CONTAINER_VIDEO_MP4: "Somente vídeo (MP4)", CONTAINER_VIDEO_ORIGINAL: "Somente vídeo (original)",
}
AUDIO_ONLY_LABEL = "Áudio"


def container_kind(container: str) -> str:
    if container in AUDIO_CONTAINERS:
        return KIND_AUDIO
    if container in VIDEO_ONLY_CONTAINERS:
        return KIND_VIDEO
    return KIND_AV


def quality_label(height: int) -> str:
    if height == BEST_QUALITY:
        return "Melhor disponível"
    if height == 2160:
        return "2160p (4K)"
    if height == 1440:
        return "1440p (2K)"
    return f"{height}p"


@dataclass(frozen=True)
class QualityOption:
    height: int  # BEST_QUALITY (0) means "best available"
    estimated_size: int | None = None

    @property
    def label(self) -> str:
        return quality_label(self.height)


@dataclass
class VideoInfo:
    url: str
    title: str | None = None
    duration: float | None = None
    uploader: str | None = None
    thumbnail_url: str | None = None
    extractor: str | None = None
    max_height: int | None = None
    source_formats: list[str] = field(default_factory=list)
    qualities: list[QualityOption] = field(default_factory=lambda: [QualityOption(BEST_QUALITY)])
    containers: list[str] = field(default_factory=lambda: [CONTAINER_ORIGINAL])
    audio_size: int | None = None
    audio_format: str | None = None  # e.g. "MP3" (converted) or "M4A" (original stream)
    # Extra options, only filled when the source really offers them.
    audio_containers: list[str] = field(default_factory=list)      # audio_m4a / audio_original
    audio_original_format: str | None = None                       # extension of the best audio stream
    video_only_containers: list[str] = field(default_factory=list)  # video_mp4 / video_original
    video_only_qualities: list[QualityOption] = field(default_factory=list)
    video_only_formats: list[str] = field(default_factory=list)

    @property
    def estimated_size(self) -> int | None:
        return self.qualities[0].estimated_size if self.qualities else None

    def estimated_size_for(self, height: int, container: str = CONTAINER_MP4) -> int | None:
        kind = container_kind(container)
        if kind == KIND_AUDIO:
            return self.audio_size
        options = self.video_only_qualities if kind == KIND_VIDEO else self.qualities
        return next((q.estimated_size for q in options if q.height == height), None)

    def all_containers(self) -> list[str]:
        """Every download option offered for this URL, in display order."""
        extra_audio = [c for c in self.audio_containers if c not in self.containers]
        return list(self.containers) + extra_audio + list(self.video_only_containers)

    def kinds(self) -> list[str]:
        available = {container_kind(c) for c in self.all_containers()}
        return [k for k in (KIND_AV, KIND_VIDEO, KIND_AUDIO) if k in available]

    def containers_for(self, kind: str) -> list[str]:
        return [c for c in self.all_containers() if container_kind(c) == kind]

    def qualities_for(self, kind: str) -> list[QualityOption]:
        if kind == KIND_AUDIO:
            return []
        if kind == KIND_VIDEO:
            return self.video_only_qualities
        return self.qualities


def container_label(key: str, info: VideoInfo) -> str:
    """Full label (used where a single combo lists every option)."""
    if key == CONTAINER_ORIGINAL:
        originals = [ext.upper() for ext in info.source_formats if ext != "mp4"]
        return f"Formato original ({', '.join(originals)})" if originals else "Formato original"
    if key == CONTAINER_AUDIO:
        return f"Somente áudio ({info.audio_format})" if info.audio_format else "Somente áudio"
    if key == CONTAINER_AUDIO_ORIGINAL and info.audio_original_format:
        return f"Somente áudio (original, {info.audio_original_format})"
    if key == CONTAINER_VIDEO_ORIGINAL:
        originals = [ext.upper() for ext in info.video_only_formats if ext != "mp4"]
        return f"Somente vídeo (original, {', '.join(originals)})" if originals else "Somente vídeo (original)"
    return CONTAINER_LABELS.get(key, key.upper())


def format_label(key: str, info: VideoInfo) -> str:
    """Short label for the "Formato" combo, once the kind was chosen separately."""
    if key in (CONTAINER_MP4, CONTAINER_VIDEO_MP4):
        return "MP4"
    if key == CONTAINER_AUDIO:
        return info.audio_format or "Original"
    if key == CONTAINER_AUDIO_M4A:
        return "M4A"
    if key == CONTAINER_AUDIO_ORIGINAL:
        return f"Original ({info.audio_original_format})" if info.audio_original_format else "Original"
    formats = info.video_only_formats if key == CONTAINER_VIDEO_ORIGINAL else info.source_formats
    originals = [ext.upper() for ext in formats if ext != "mp4"]
    return f"Original ({', '.join(originals)})" if originals else "Original"
