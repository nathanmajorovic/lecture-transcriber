#!/usr/bin/env bash
# Commandoregel-versie voor macOS/Linux.
#   ./lecture-transcribe.sh recording.mp4 --course ml --slides lecture3.pdf
VENV="$HOME/.venvs/lecture-transcriber"
if [ ! -x "$VENV/bin/python" ]; then
    echo "De app is nog niet geïnstalleerd. Draai eerst:  ./install.sh"
    exit 1
fi
exec "$VENV/bin/python" -m lecture_transcriber "$@"
