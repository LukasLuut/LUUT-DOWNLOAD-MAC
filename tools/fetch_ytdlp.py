"""Place the official yt-dlp.exe next to the built executable (dist/bin), verified like any in-app update.

    python tools/fetch_ytdlp.py [destination folder] [--version 2026.08.19]

Uses the application's own YtDlpManager (official GitHub release + SHA2-256SUMS + `--version` check), so the build
and the app share one implementation. The copy in dist/bin is only a seed: on first run the app copies it to the
user's profile, where it is updated without rebuilding Luut Video Downloader.exe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.services.ytdlp_manager import UpdateStatus, YtDlpError, YtDlpManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", nargs="?", default=str(ROOT / "dist" / "bin"))
    parser.add_argument("--version", help="specific official release (default: latest)")
    args = parser.parse_args()
    manager = YtDlpManager(Path(args.destination))
    try:
        if args.version:
            release = manager.source.release(args.version)
            if manager.get_installed_version() != release.version:
                manager.install(release)
        else:
            check = manager.check_for_updates()
            if check.status == UpdateStatus.CHECK_FAILED:
                if manager.get_installed_version():
                    print(f"Sem conexão; mantendo yt-dlp {manager.get_installed_version()}")
                    return 0
                print(f"Não foi possível obter o yt-dlp: {check.error}")
                return 1
            if check.status != UpdateStatus.UP_TO_DATE:
                manager.install(check.release)
    except YtDlpError as error:
        print(f"Falha: {error.message} ({error.detail})")
        return 1
    print(f"yt-dlp {manager.get_installed_version(refresh=True)}: {manager.managed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
