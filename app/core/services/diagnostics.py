"""Environment diagnostics shown in the Diagnóstico screen."""

from __future__ import annotations

import platform
import socket
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app import APP_VERSION
from app.core.models.settings import AppSettings
from app.core.errors import AppError
from app.core.providers.base import DownloadProvider
from app.infrastructure import filesystem as fs
from app.infrastructure.database import Database
from app.infrastructure.tools import ffmpeg_version, find_ffmpeg, find_js_runtime
from app.utils.formatters import format_bytes

LOW_SPACE_WARNING = 2 * 1024**3
LOW_SPACE_ERROR = 200 * 1024**2


class Level(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class DiagnosticItem:
    label: str
    value: str
    level: Level = Level.OK


def _qt_version() -> str:
    try:
        import PySide6
    except ImportError:
        return "Não instalado"
    return PySide6.__version__


def _folder_item(label: str, folder: Path) -> DiagnosticItem:
    try:
        fs.ensure_writable_dir(folder)
    except AppError as error:
        return DiagnosticItem(label, f"{folder} — {error.user_message}", Level.ERROR)
    return DiagnosticItem(label, f"{folder} — gravável", Level.OK)


def _space_item(folder: Path) -> DiagnosticItem:
    free = fs.free_space(folder)
    if free is None:
        return DiagnosticItem("Espaço disponível", "Não disponível", Level.WARNING)
    level = Level.ERROR if free < LOW_SPACE_ERROR else Level.WARNING if free < LOW_SPACE_WARNING else Level.OK
    return DiagnosticItem("Espaço disponível", format_bytes(free), level)


def _internet_item() -> DiagnosticItem:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        return DiagnosticItem("Conexão com a internet", "Sem conexão", Level.WARNING)
    return DiagnosticItem("Conexão com a internet", "Conectado", Level.OK)


def ytdlp_items(settings: AppSettings, manager) -> list[DiagnosticItem]:
    from app.core.services.ytdlp_manager import compare_versions, parse_version

    path = manager.get_executable_path() if manager else None
    version = manager.get_installed_version(refresh=True) if path else None
    latest = settings.ytdlp_latest_known or None
    available = bool(version and latest and parse_version(latest) and compare_versions(latest, version) > 0)
    last = settings.ytdlp_last_check.replace("T", " ") if settings.ytdlp_last_check else "nunca"
    return [
        DiagnosticItem("yt-dlp", version or ("não responde — reinstale em Configurações → Atualizações" if path else
                                              "não instalado"), Level.OK if version else Level.ERROR),
        DiagnosticItem("yt-dlp encontrado", "SIM" if path else "NÃO", Level.OK if path else Level.ERROR),
        DiagnosticItem("Caminho do yt-dlp", str(path or manager.managed_path if manager else "—")),
        DiagnosticItem("Última verificação do yt-dlp", last),
        DiagnosticItem("Atualização do yt-dlp disponível", f"SIM ({latest})" if available else "NÃO",
                       Level.WARNING if available else Level.OK),
    ]


def run_diagnostics(settings: AppSettings, provider: DownloadProvider, db: Database) -> list[DiagnosticItem]:
    download_dir = Path(settings.default_dir)
    ffmpeg = find_ffmpeg()
    js_runtime = find_js_runtime()
    db_ok = db.integrity_ok()
    return [
        DiagnosticItem("Luut Video Downloader", APP_VERSION),
        DiagnosticItem("Python", f"{platform.python_version()} ({'embutido' if getattr(sys, 'frozen', False) else 'sistema'})"),
        DiagnosticItem("Sistema operacional", f"{platform.system()} {platform.release()} ({platform.version()})"),
        DiagnosticItem("Interface (PySide6)", _qt_version()),
        *ytdlp_items(settings, getattr(provider, "manager", None)),
        DiagnosticItem("FFmpeg", (ffmpeg_version() or "versão desconhecida") if ffmpeg else
                       "Não encontrado — somente formatos com áudio e vídeo juntos", Level.OK if ffmpeg else Level.WARNING),
        DiagnosticItem("Runtime JavaScript (Deno)", "Encontrado" if js_runtime else
                       "Não encontrado — alguns formatos do YouTube podem não aparecer",
                       Level.OK if js_runtime else Level.WARNING),
        _folder_item("Pasta de download", download_dir),
        _space_item(download_dir),
        _folder_item("Diretório temporário", Path(settings.temp_dir)),
        DiagnosticItem("Banco de dados", "Íntegro" if db_ok else "Problemas detectados",
                       Level.OK if db_ok else Level.ERROR),
        _internet_item(),
    ]
