"""
Audio: opname onderzoeken (video of enkel audio?), omzetten naar 16 kHz mono WAV
met ffmpeg, en stukken daarvan inlezen als numpy-array.

De opname gaat NOOIT rechtstreeks naar faster-whisper (PyAV-decodeerfouten).
De WAV wordt per chunk ingelezen met `wave` + seek, nooit volledig.
"""
from __future__ import annotations

import json
import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .system import FriendlyError, find_ffmpeg, subprocess_kwargs

SR = 16000
WAV_BYTES_PER_S = SR * 2  # mono int16


@dataclass
class MediaInfo:
    path: Path
    duration: float
    has_video: bool
    width: int | None = None
    height: int | None = None
    has_audio: bool = True

    @property
    def kind(self) -> str:
        return "video" if self.has_video else "audio"


def probe(path: str | Path) -> MediaInfo:
    """Kijk wat er in het bestand zit. Een 'video'-stroom die enkel een albumhoesje is
    (attached_pic) telt niet als video; een .mp4 met enkel audio is dus 'audio'."""
    path = Path(path)
    if not path.is_file():
        raise FriendlyError(f"Bestand niet gevonden: {path}")
    ff = find_ffmpeg()
    r = subprocess.run(
        [ff["ffprobe"], "-v", "error", "-print_format", "json",
         "-show_entries", "format=duration:stream=codec_type,width,height:stream_disposition=attached_pic",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **subprocess_kwargs())
    if r.returncode != 0:
        raise FriendlyError(f"ffprobe kan '{path.name}' niet lezen. Is het een geldig audio- of "
                            f"videobestand?\n{r.stderr.strip()[:300]}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise FriendlyError(f"'{path.name}' bevat geen audiospoor.")
    try:
        dur = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise FriendlyError(f"Kan de duur van '{path.name}' niet bepalen.")
    v = video[0] if video else {}
    return MediaInfo(path, dur, bool(video), v.get("width"), v.get("height"), True)


def extract_wav(src: Path, dst: Path, threads: int, start: float | None = None,
                duration: float | None = None, cancel_check=None) -> Path:
    """Zet `src` om naar 16 kHz mono 16-bit WAV. Schrijft eerst naar een .part-bestand
    en hernoemt pas als ffmpeg klaar is: een half bestand wordt dus nooit hergebruikt."""
    dst = Path(dst)
    if dst.is_file() and dst.stat().st_size > 44:
        return dst  # al eerder gemaakt (hervatten)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part.wav")
    ff = find_ffmpeg()
    cmd = [ff["ffmpeg"], "-nostdin", "-v", "error", "-y", "-threads", str(threads)]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **subprocess_kwargs())
    try:
        while True:
            try:
                _, err = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_check:
                    cancel_check()  # gooit Cancelled
    except BaseException:
        proc.kill()
        proc.wait()
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise FriendlyError(f"ffmpeg kon de audio uit '{Path(src).name}' niet omzetten:\n"
                            f"{(err or b'').decode('utf-8', 'replace').strip()[:400]}")
    os.replace(tmp, dst)
    return dst


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def read_wav(path: Path, start_s: float, dur_s: float) -> np.ndarray:
    """Lees [start, start+dur) als float32 in [-1, 1]. Leest enkel dat stuk van schijf."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise FriendlyError(f"Onverwacht WAV-formaat in {path.name}.")
        n = w.getnframes()
        a = max(0, min(n, int(round(start_s * SR))))
        b = max(a, min(n, int(round((start_s + dur_s) * SR))))
        w.setpos(a)
        raw = w.readframes(b - a)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def quiet_boundary(path: Path, nominal_s: float, search_s: float = 15.0, win_s: float = 0.5) -> float:
    """Zoek rond `nominal_s` het stilste moment, zodat een chunkgrens niet midden in een
    woord valt. Leest maar ±30 s audio in."""
    lo = max(0.0, nominal_s - search_s)
    x = read_wav(path, lo, 2 * search_s)
    win = int(win_s * SR)
    if len(x) < 2 * win:
        return nominal_s
    nwin = len(x) // win
    e = (x[: nwin * win].reshape(nwin, win) ** 2).mean(axis=1)
    i = int(np.argmin(e))
    return lo + (i + 0.5) * win_s


def chunk_bounds(path: Path, duration: float, chunk_s: float) -> list[tuple[float, float]]:
    """Verdeel de opname in chunks van ±chunk_s, met grenzen op stille momenten."""
    if duration <= chunk_s * 1.25:
        return [(0.0, duration)]
    cuts = [0.0]
    t = chunk_s
    while t < duration - chunk_s * 0.25:
        cuts.append(quiet_boundary(path, t))
        t = cuts[-1] + chunk_s
    cuts.append(duration)
    return [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
