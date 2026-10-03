"""Luut Video Downloader."""

import sys

APP_NAME = "Luut Video Downloader"
APP_VERSION = "1.3.0"
APP_AUTHOR = "Luut"
APP_ID = "LuutVideoDownloader"
APP_USER_MODEL_ID = "Luut.VideoDownloader"
# Operating system name shown in the interface ("Iniciar com o Windows" / "Iniciar com o macOS").
SYSTEM_NAME = "macOS" if sys.platform == "darwin" else "Windows"

# URL of a JSON manifest describing the latest release. Empty = update checks disabled.
UPDATE_MANIFEST_URL = ""
