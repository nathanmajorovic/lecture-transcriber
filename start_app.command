#!/usr/bin/env bash
# Start de Lecture Transcriber op macOS. Dubbelklik in Finder, of: ./start_app.command
cd "$(dirname "$0")"
VENV="$HOME/.venvs/lecture-transcriber"
if [ ! -x "$VENV/bin/python" ]; then
    echo "De app is nog niet geïnstalleerd. Draai eerst:  ./install.sh"
    read -r -p "Druk op Enter om te sluiten ..."
    exit 1
fi
exec "$VENV/bin/python" -m lecture_transcriber.gui
