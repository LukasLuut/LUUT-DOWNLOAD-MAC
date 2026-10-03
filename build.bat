@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   Luut Video Downloader - build
echo ==========================================

set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" (
    echo [1/7] Ambiente virtual encontrado.
) else (
    echo [1/7] Criando ambiente virtual...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv || goto :error
    ) else (
        python -m venv .venv || goto :error
    )
)

echo [2/7] Instalando dependencias...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q || goto :error
"%VPY%" -m pip install -r requirements-dev.txt --disable-pip-version-check -q || goto :error

echo [3/7] Executando testes...
set "QT_QPA_PLATFORM=offscreen"
"%VPY%" -m pytest -q || goto :error
set "QT_QPA_PLATFORM="

echo [4/7] Preparando recursos (icone, ffmpeg)...
"%VPY%" tools\prepare_build.py || goto :error

echo [5/7] Gerando executavel...
"%VPY%" -m PyInstaller --noconfirm --clean "Luut Video Downloader.spec" || goto :error

echo [6/7] Baixando o yt-dlp oficial para dist\bin (atualizavel sem recompilar)...
"%VPY%" tools\fetch_ytdlp.py "dist\bin" || goto :error

echo [7/7] Verificando que o executavel nao contem dados de desenvolvimento...
"%VPY%" tools\verify_bundle.py || goto :error

echo.
echo Build concluido: "dist\Luut Video Downloader.exe" + "dist\bin\yt-dlp.exe"
exit /b 0

:error
echo.
echo Falha no build. Veja as mensagens acima.
exit /b 1
