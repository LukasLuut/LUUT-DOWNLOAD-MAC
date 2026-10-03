"""Logging to logs/app.log, logs/downloads.log and logs/errors.log with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER = "luut"
DOWNLOADS_LOGGER = "luut.downloads"

_SECRET_PARAMS = re.compile(
    r"(?i)\b(token|access_token|refresh_token|sig|signature|key|api_key|apikey|auth|password|passwd|pwd|"
    r"session|sessionid|cookie|jwt|secret|expire|ei|ip|lsig|n)=([^&\s\"']+)"
)
_AUTH_HEADER = re.compile(r"(?i)(authorization|cookie|set-cookie)\s*[:=]\s*[^\s,;]+")
_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")
_URL = re.compile(r"(?i)\b(https?://)(?:[^/\s:@]+(?::[^/\s@]*)?@)?([^/\s?#,;'\"]+)[^\s,;'\"]*")


def redact(text: str) -> str:
    """Strip credentials/tokens that may appear inside URLs or messages."""
    text = _USERINFO.sub(r"\1***:***@", text)
    text = _AUTH_HEADER.sub(r"\1=***", text)
    return _SECRET_PARAMS.sub(r"\1=***", text)


def mask_urls(text: str) -> str:
    """Keep only scheme + host of every URL. Used for content URLs (they may come from the user's clipboard)."""
    return _URL.sub(lambda m: f"{m.group(1)}{m.group(2)}/…", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _file_handler(path: Path, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True)
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    return handler


def setup_logging(logs_dir: Path, verbose: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger(ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False

    root.addHandler(_file_handler(logs_dir / "app.log", logging.DEBUG))
    root.addHandler(_file_handler(logs_dir / "errors.log", logging.ERROR))

    downloads = logging.getLogger(DOWNLOADS_LOGGER)
    for handler in list(downloads.handlers):
        downloads.removeHandler(handler)
        handler.close()
    downloads.addHandler(_file_handler(logs_dir / "downloads.log", logging.DEBUG))


def set_verbose(verbose: bool) -> None:
    logging.getLogger(ROOT_LOGGER).setLevel(logging.DEBUG if verbose else logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")
