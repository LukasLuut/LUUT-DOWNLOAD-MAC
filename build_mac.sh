#!/usr/bin/env bash
# Luut Video Downloader - build for macOS (.app + .dmg). Runs on a Mac or on the GitHub Actions macOS runners.
# Apple Silicon (arm64) only: the official yt-dlp_macos no longer runs on Intel Macs.
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(uname -m)" != "arm64" ]; then
    echo "Este build exige um Mac com Apple Silicon (arm64): o yt-dlp oficial para macOS nao roda em Macs Intel."
    exit 1
fi

APP_NAME="Luut Video Downloader"
PYTHON="${PYTHON:-python3}"
ARCH="$(uname -m)"

echo "=========================================="
echo "  ${APP_NAME} - build macOS (${ARCH})"
echo "=========================================="

if [ -x ".venv/bin/python" ]; then
    echo "[1/8] Ambiente virtual encontrado."
else
    echo "[1/8] Criando ambiente virtual..."
    "$PYTHON" -m venv .venv
fi
VPY=".venv/bin/python"

echo "[2/8] Instalando dependencias..."
"$VPY" -m pip install --upgrade pip --disable-pip-version-check -q
"$VPY" -m pip install -r requirements-dev.txt --disable-pip-version-check -q

echo "[3/8] Executando testes..."
QT_QPA_PLATFORM=offscreen "$VPY" -m pytest -q

echo "[4/8] Preparando recursos (icone .icns, ffmpeg)..."
"$VPY" tools/prepare_build.py

echo "[5/8] Baixando o yt-dlp oficial (yt-dlp_macos) para dentro do app..."
"$VPY" tools/fetch_ytdlp.py build/bundle/bin

echo "[6/8] Gerando ${APP_NAME}.app..."
"$VPY" -m PyInstaller --noconfirm --clean "${APP_NAME} macOS.spec"
APP="dist/${APP_NAME}.app"
# PyInstaller signs the bundle ad-hoc (required to run on Apple Silicon; no Apple Developer account): check it.
codesign --verify --deep --verbose=1 "$APP"

echo "[7/8] Verificando o pacote..."
"$VPY" tools/verify_bundle.py
# Smoke test of the frozen app in an isolated profile: it must find its bundled yt-dlp and run it (--version).
SMOKE="$(mktemp -d)"
LUUT_DATA_DIR="$SMOKE/data" "$APP/Contents/MacOS/${APP_NAME}" --ytdlp-status "$SMOKE/report.json"
cat "$SMOKE/report.json"; echo
"$VPY" -c "import json, sys; r = json.load(open(sys.argv[1])); sys.exit(0 if r.get('installed_after') else 'yt-dlp do app nao executou')" "$SMOKE/report.json"
rm -rf "$SMOKE"

echo "[8/8] Gerando instalador .dmg..."
VERSION="$("$VPY" -c 'from app import APP_VERSION; print(APP_VERSION)')"
DMG="dist/LuutVideoDownloader-${VERSION}-macOS-${ARCH}.dmg"
STAGING="$(mktemp -d)"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"   # drag-and-drop install
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"

echo
echo "Build concluido: ${DMG}"
