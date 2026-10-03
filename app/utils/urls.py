"""URL validation helpers. URLs are only ever passed as data, never executed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$", re.I)
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def normalize_url(text: str | None) -> str | None:
    """Return a cleaned http(s) URL, or None when the text is not a plausible web URL."""
    if not text:
        return None
    candidate = text.strip().strip("<>\"'").strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH or any(ch.isspace() for ch in candidate):
        return None
    if candidate.lower().startswith("www."):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - raises ValueError on invalid ports
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if not (_HOST_RE.match(host) or _IPV4_RE.match(host) or host == "localhost"):
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, parts.fragment))


def is_valid_url(text: str | None) -> bool:
    return normalize_url(text) is not None


@dataclass(frozen=True)
class ParsedLine:
    line_number: int
    raw: str
    url: str | None

    @property
    def is_valid(self) -> bool:
        return self.url is not None


def parse_url_lines(text: str) -> list[ParsedLine]:
    """Parse one URL per line; blank lines and `#` comments are skipped, duplicates dropped."""
    seen: set[str] = set()
    result: list[ParsedLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        url = normalize_url(raw)
        if url is not None:
            if url in seen:
                continue
            seen.add(url)
        result.append(ParsedLine(number, raw, url))
    return result
