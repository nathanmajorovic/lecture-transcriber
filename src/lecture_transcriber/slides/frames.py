"""
Frames uit de video halen met ffmpeg: 1 frame per `interval` s, grijswaarden,
lage resolutie. De frames gaan via een pipe rechtstreeks naar een numpy-memmap
op schijf (_work/frames.u8): de video wordt nooit in het geheugen geladen en
ook de frames staan niet allemaal tegelijk in het RAM.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..system import FriendlyError, find_ffmpeg, subprocess_kwargs


@dataclass
class FrameStore:
    path: Path
    n: int
    h: int
    w: int
    interval: float
    t0: float            # tijd (in de opname) van frame 0
    src_w: int
    src_h: int

    def open(self) -> np.memmap:
        return np.memmap(self.path, dtype=np.uint8, mode="r", shape=(self.n, self.h, self.w))

    def time(self, i: int) -> float:
        return self.t0 + i * self.interval


def extract_frames(video: Path, work: Path, tag: str, src_w: int, src_h: int, duration: float,
                   threads: int, interval: float = 2.0, width: int = 640,
                   start: float | None = None, clip: float | None = None,
                   progress=None, cancel_check=None) -> FrameStore:
    w = width
    h = int(round(src_h * width / src_w / 2)) * 2
    data = work / f"{tag}_frames.u8"
    meta = work / f"{tag}_frames.json"
    if meta.is_file() and data.is_file():
        m = json.loads(meta.read_text())
        if m.get("complete") and m["w"] == w and m["h"] == h and m["interval"] == interval:
            return FrameStore(data, m["n"], h, w, interval, m["t0"], src_w, src_h)

    span = clip if clip else duration - (start or 0)
    n_max = int(span / interval) + 2
    mm = np.memmap(data, dtype=np.uint8, mode="w+", shape=(n_max, h, w))
    ff = find_ffmpeg()
    cmd = [ff["ffmpeg"], "-nostdin", "-v", "error", "-threads", str(threads)]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(video)]
    if clip:
        cmd += ["-t", f"{clip:.3f}"]
    # fps-filter: neemt het frame op t = (k + 0.5) * interval
    cmd += ["-an", "-sn", "-vf", f"fps=1/{interval},scale={w}:{h}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=w * h * 4, **subprocess_kwargs())
    frame_bytes = w * h
    n = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            if n < n_max:
                mm[n] = np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
                n += 1
            if progress and n % 25 == 0:
                progress(min(1.0, n * interval / span))
            if cancel_check and n % 10 == 0:
                cancel_check()
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        del mm
        raise
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    mm.flush()
    del mm
    if proc.returncode != 0 or n == 0:
        raise FriendlyError(f"ffmpeg kon geen beelden uit de video halen: {err.strip()[:300]}")
    meta.write_text(json.dumps({"n": n, "w": w, "h": h, "interval": interval,
                                "t0": (start or 0.0) + interval / 2, "complete": True}))
    return FrameStore(data, n, h, w, interval, (start or 0.0) + interval / 2, src_w, src_h)


def grab_color_frame(video: Path, t: float, out_png: Path,
                     crop: tuple[int, int, int, int] | None = None) -> bool:
    """Eén frame in volle resolutie en kleur bewaren (voor schermafbeeldingen)."""
    ff = find_ffmpeg()
    vf = []
    if crop:
        x, y, cw, ch = crop
        vf.append(f"crop={cw}:{ch}:{x}:{y}")
    cmd = [ff["ffmpeg"], "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
           "-i", str(video), "-frames:v", "1"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [str(out_png)]
    r = subprocess.run(cmd, capture_output=True, **subprocess_kwargs())
    return r.returncode == 0 and out_png.is_file()


def cleanup(work: Path, tag: str) -> None:
    for suffix in ("_frames.u8", "_frames.json"):
        try:
            os.remove(work / f"{tag}{suffix}")
        except OSError:
            pass
