"""Windows-safe file naming."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

INVALID_CHARS = '\\/:*?"<>|'
_INVALID_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_SPACES_RE = re.compile(r"\s+")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_STEM_LENGTH = 150
MAX_PATH_LENGTH = 250


def sanitize_filename(name: str | None, max_length: int = MAX_STEM_LENGTH, fallback: str = "video") -> str:
    """Turn arbitrary text (e.g. a video title) into a safe Windows file name stem."""
    text = unicodedata.normalize("NFC", name or "")
    text = _INVALID_RE.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    text = _SPACES_RE.sub(" ", text).strip()
    text = text.lstrip(".")
    if len(text) > max_length:
        text = text[:max_length]
    text = text.rstrip(" .")
    if not text:
        return fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def unique_path(directory: Path, stem: str, extension: str) -> Path:
    """First non-existing `stem.ext`, `stem (1).ext`, `stem (2).ext`... in directory."""
    ext = extension if not extension or extension.startswith(".") else f".{extension}"
    room = MAX_PATH_LENGTH - len(str(directory)) - len(ext) - 8
    if room < 10:
        raise ValueError("Destination path is too long")
    stem = stem[:room].rstrip(" .") or "video"
    candidate = directory / f"{stem}{ext}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){ext}"
        counter += 1
    return candidate


def safe_join(base: Path, *parts: str) -> Path:
    """Join sanitized path components and guarantee the result stays inside `base`."""
    cleaned = [sanitize_filename(part, max_length=80, fallback="_") for part in parts]
    target = base.joinpath(*cleaned).resolve()
    base_resolved = base.resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError("Path escapes the base directory")
    return target
