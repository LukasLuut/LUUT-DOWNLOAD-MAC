"""Fail the build if the executable contains development data (databases, logs, tests, screenshots...)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(__file__).resolve().parents[1]
IS_MAC = sys.platform == "darwin"
APP_BUNDLE = ROOT / "dist" / "Luut Video Downloader.app"
EXE = APP_BUNDLE / "Contents" / "MacOS" / "Luut Video Downloader" if IS_MAC else ROOT / "dist" / "Luut Video Downloader.exe"
FORBIDDEN = re.compile(
    r"(\.db$|\.db-wal$|\.db-shm$|\.sqlite$|\.log$|\.part$|self-test\.json$|(^|[\\/])tests?[\\/]|"
    r"(^|[\\/])docs[\\/]|screenshot|(^|[\\/])(dist|build)[\\/]|\.pytest_cache)",
    re.IGNORECASE)
# yt_dlp must stay outside the executable (it is the independently updatable bin/yt-dlp.exe / yt-dlp_macos).
FORBIDDEN_MODULE = re.compile(r"^module:(tests|conftest|yt_dlp|yt_dlp_ejs)(\.|$)")


def entries(exe: Path) -> list[str]:
    """Data files of the one-file archive plus the Python modules inside its PYZ. On macOS (one-folder .app) the files
    of the bundle itself are included too, relative to Contents/."""
    archive = CArchiveReader(str(exe))
    names = list(archive.toc)
    if IS_MAC:
        contents = APP_BUNDLE / "Contents"
        names += [p.relative_to(contents).as_posix() for p in contents.rglob("*") if p.is_file()]
    for name in list(names):
        if name.endswith(".pyz") or name.startswith("PYZ"):
            pyz = archive.open_embedded_archive(name)
            if isinstance(pyz, ZlibArchiveReader):
                names += [f"module:{module}" for module in pyz.toc]
    return names


def main() -> int:
    if not EXE.is_file():
        print(f"Executável não encontrado: {EXE}")
        return 1
    names = entries(EXE)
    bad = [n for n in names if (FORBIDDEN_MODULE if n.startswith("module:") else FORBIDDEN).search(n)]
    app_assets = sorted(n for n in names if re.search(r"(^|[\\/])assets[\\/]", n))
    print(f"{len(names)} entradas no pacote; {len(app_assets)} assets do app.")
    if bad:
        print("Dados de desenvolvimento encontrados no pacote:")
        for name in bad:
            print("  -", name)
        return 1
    print("OK: nenhum banco, log, teste, screenshot ou dado temporário no executável.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
