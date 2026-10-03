"""Application errors with user-friendly (pt-BR) messages. Tracebacks never reach the user."""

from __future__ import annotations

import errno
from enum import Enum


class ErrorKind(str, Enum):
    INVALID_URL = "invalid_url"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    DRM = "drm"
    LIVE = "live"
    PLAYLIST = "playlist"
    NETWORK = "network"
    DISK_FULL = "disk_full"
    PERMISSION = "permission"
    DESTINATION = "destination"
    ENGINE_MISSING = "engine_missing"
    UNEXPECTED = "unexpected"


FRIENDLY_MESSAGES: dict[ErrorKind, str] = {
    ErrorKind.INVALID_URL: "O link informado não parece ser válido.",
    ErrorKind.UNSUPPORTED: "Não foi possível analisar este link.",
    ErrorKind.UNAVAILABLE: "Não foi possível acessar este conteúdo.",
    ErrorKind.DRM: "Este conteúdo é protegido (DRM) e não pode ser baixado.",
    ErrorKind.LIVE: "Transmissões ao vivo não são suportadas.",
    ErrorKind.PLAYLIST: "Este link é uma playlist. Use “Adicionar vários links” com os vídeos individuais.",
    ErrorKind.NETWORK: "A conexão foi interrompida. Tente novamente.",
    ErrorKind.DISK_FULL: "Não há espaço suficiente no disco selecionado.",
    ErrorKind.PERMISSION: "O aplicativo não possui permissão para salvar nesta pasta.",
    ErrorKind.DESTINATION: "A pasta de destino não está disponível.",
    ErrorKind.ENGINE_MISSING: "Não foi possível localizar o yt-dlp. Instale-o em Configurações → Atualizações.",
    ErrorKind.UNEXPECTED: "Ocorreu um erro inesperado. Consulte os logs para mais detalhes.",
}

TRANSIENT_KINDS = frozenset({ErrorKind.NETWORK})


class AppError(Exception):
    """Error carrying a category; `detail` is for logs only."""

    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail

    @property
    def user_message(self) -> str:
        return FRIENDLY_MESSAGES[self.kind]

    @property
    def is_transient(self) -> bool:
        return self.kind in TRANSIENT_KINDS


def classify_os_error(exc: OSError) -> AppError:
    if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112):
        return AppError(ErrorKind.DISK_FULL, str(exc))
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return AppError(ErrorKind.PERMISSION, str(exc))
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)) or getattr(exc, "winerror", None) in (3, 21, 53):
        return AppError(ErrorKind.DESTINATION, str(exc))
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return AppError(ErrorKind.NETWORK, str(exc))
    return AppError(ErrorKind.UNEXPECTED, str(exc))


def to_app_error(exc: BaseException) -> AppError:
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, OSError):
        return classify_os_error(exc)
    return AppError(ErrorKind.UNEXPECTED, f"{type(exc).__name__}: {exc}")
