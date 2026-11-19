@echo off
rem Start de Lecture Transcriber (venster, zonder terminal).
set "VENV=%USERPROFILE%\.venvs\lecture-transcriber"
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo De app is nog niet geinstalleerd. Dubbelklik eerst op install.bat.
    pause
    exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" -m lecture_transcriber.gui
