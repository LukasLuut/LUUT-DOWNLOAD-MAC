"""Human-friendly (pt-BR) formatting."""

from __future__ import annotations

from datetime import datetime

NOT_AVAILABLE = "Não disponível"

MONTHS_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
             "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

_UNITS = ["B", "KB", "MB", "GB", "TB"]


def _decimal(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def format_bytes(size: float | None) -> str:
    if size is None or size < 0:
        return NOT_AVAILABLE
    value = float(size)
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    if unit == 0:
        return f"{int(value)} B"
    return f"{_decimal(value, 1 if value < 100 else 0)} {_UNITS[unit]}"


def format_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "—"
    return f"{format_bytes(bytes_per_second)}/s"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return NOT_AVAILABLE
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    return format_duration(seconds)


def format_datetime(value: str | None) -> str:
    if not value:
        return NOT_AVAILABLE
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return NOT_AVAILABLE


def or_not_available(value: object | None) -> str:
    if value is None:
        return NOT_AVAILABLE
    text = str(value).strip()
    return text or NOT_AVAILABLE


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
