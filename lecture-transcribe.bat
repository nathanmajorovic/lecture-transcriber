@echo off
rem Commandoregel-versie. Je kan ook opnames op dit bestand slepen.
rem   lecture-transcribe.bat opname.mp4 --course ml --slides les3.pdf
chcp 65001 >nul
set "VENV=%USERPROFILE%\.venvs\lecture-transcriber"
if not exist "%VENV%\Scripts\python.exe" (
    echo De app is nog niet geinstalleerd. Dubbelklik eerst op install.bat.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m lecture_transcriber %*
if "%~1" neq "" if exist "%~1" pause
