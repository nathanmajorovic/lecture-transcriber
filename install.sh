#!/usr/bin/env bash
# Eenmalige installatie op macOS: ffmpeg, Python, virtuele omgeving, pakketten, modellen.
# Gebruik:  ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
VENV="$HOME/.venvs/lecture-transcriber"

echo "=== 1/5  Homebrew en Python controleren"
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew niet gevonden. Installeer het eerst via https://brew.sh en draai dit script opnieuw."
    exit 1
fi
PY=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY="$(command -v "$c")"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "Geen Python 3.10+ met Tkinter gevonden: installeren met Homebrew ..."
    brew install python@3.12 python-tk@3.12
    PY="$(brew --prefix)/bin/python3.12"
fi
echo "Python: $PY ($("$PY" --version))"

echo "=== 2/5  ffmpeg controleren"
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg niet gevonden: installeren met Homebrew ..."
    brew install ffmpeg
fi

echo "=== 3/5  Virtuele omgeving in $VENV"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/python" -m pip install -r requirements.txt
"$VENV/bin/python" -m pip install -e . --no-deps -q

echo "=== 4/5  Modellen downloaden (eenmalig, ±2 GB)"
if [ "$(uname -m)" = "arm64" ]; then
    echo "Apple Silicon: mlx-whisper (gebruikt de GPU van de Mac)"
    "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as d; print(d('mlx-community/whisper-large-v3-turbo'))"
else
    echo "Intel-Mac: geen GPU-ondersteuning, de app gebruikt de CPU (model 'small')."
fi
"$VENV/bin/python" -c "from faster_whisper.utils import download_model as d; print('small ->', d('small'))"

echo "=== 5/5  Startbestanden uitvoerbaar maken"
chmod +x start_app.command lecture-transcribe.sh

echo
echo "Klaar. Start de app door te dubbelklikken op start_app.command (of: ./start_app.command)."
