@echo off
rem Eenmalige installatie: ffmpeg, virtuele omgeving, Python-pakketten, modellen, snelkoppeling.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\lecture-transcriber"

echo === 1/5  Python controleren
where python >nul 2>nul || (
    echo Python niet gevonden. Installeer Python 3.10 of nieuwer, bv.:  winget install Python.Python.3.12
    pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || (
    echo Python 3.10 of nieuwer is nodig.
    pause & exit /b 1
)

echo === 2/5  ffmpeg controleren
where ffmpeg >nul 2>nul || (
    echo ffmpeg niet gevonden: installeren met winget ...
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
)

echo === 3/5  Virtuele omgeving in %VENV%  (buiten OneDrive)
if not exist "%VENV%\Scripts\python.exe" python -m venv "%VENV%" || (pause & exit /b 1)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip -q
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
"%VENV%\Scripts\python.exe" -m pip install -e . --no-deps -q || (pause & exit /b 1)

echo === 4/5  Modellen downloaden (eenmalig, ±2 GB) en GPU testen
"%VENV%\Scripts\python.exe" -c "from faster_whisper.utils import download_model as d; [print(m, '->', d(m)) for m in ('large-v3-turbo', 'small')]"
"%VENV%\Scripts\python.exe" tests\cuda_check.py

echo === 5/5  Snelkoppeling op het bureaublad
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"

echo.
echo Klaar. Start de app via de snelkoppeling "Lecture Transcriber" of start_app.bat.
pause
