"""
Slidewissels detecteren.

1. Slidegebied zoeken (tenzij de gebruiker een uitsnede opgaf): pixels die
   zelden veranderen maar wel inhoud tonen (helder of variërend over de les).
   Een camerabeeld met een bewegende prof valt zo weg; het grootste
   samenhangende gebied is de slide.
2. In dat gebied: een slide telt pas als het beeld minstens `min_stable_s`
   stabiel blijft. Korte veranderingen (animatie, cursor, overgang) vallen weg.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .frames import FrameStore

FEAT_W = 192          # breedte van de vergelijkingsbeelden
PIX_DIFF = 28         # grijswaardeverschil dat als "veranderd" telt
STATIC_FRAC = 0.004   # < 0,4% veranderde pixels t.o.v. vorig frame = stilstaand
SAME_FRAC = 0.012     # < 1,2% verschil tussen twee stabiele stukken = zelfde slide


@dataclass
class StableRun:
    first: int            # eerste frame-index
    last: int             # laatste frame-index
    rep: int              # representatief frame (midden)
    feat: np.ndarray      # vergelijkingsbeeld (uint8)


@dataclass
class DetectedSlide:
    start: float          # tijd in de opname (s)
    end: float
    rep_time: float       # tijd van het representatieve frame
    rep_index: int
    feat: np.ndarray


def find_roi(store: FrameStore, progress=None) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) in frame-pixels, of None als er geen duidelijk slidegebied is."""
    mm = store.open()
    n = store.n
    if n < 10:
        return None
    sw = 160
    sh = max(1, int(round(store.h * sw / store.w)))
    step = max(1, n // 600)            # hooguit ±600 frames voor de statistiek
    idx = range(0, n, step)
    s1 = np.zeros((sh, sw), np.float64)
    s2 = np.zeros((sh, sw), np.float64)
    changes = np.zeros((sh, sw), np.float64)
    prev = None
    cnt = 0
    for k, i in enumerate(idx):
        f = cv2.resize(np.asarray(mm[i]), (sw, sh), interpolation=cv2.INTER_AREA).astype(np.float32)
        f = cv2.GaussianBlur(f, (3, 3), 0)
        s1 += f
        s2 += f * f
        if prev is not None:
            changes += np.abs(f - prev) > 12
        prev = f
        cnt += 1
        if progress and k % 50 == 0:
            progress(k / len(idx))
    del mm
    mean = s1 / cnt
    std = np.sqrt(np.maximum(0, s2 / cnt - mean * mean))
    freq = changes / max(1, cnt - 1)
    cand = ((freq < 0.10) & ((mean > 70) | (std > 25))).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    if nlab <= 1:
        return None
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[best]
    if area < 0.04 * sw * sh or w < 0.15 * sw or h < 0.15 * sh:
        return None
    # strakker maken: zwarte randen rond de slide wegknippen
    sub = mean[y:y + h, x:x + w]
    colm, rowm = sub.mean(axis=0), sub.mean(axis=1)
    cols = np.where(colm > 0.35 * colm.max())[0]
    rows = np.where(rowm > 0.35 * rowm.max())[0]
    if len(cols) > 0.15 * sw and len(rows) > 0.15 * sh:
        x, w = x + cols[0], cols[-1] - cols[0] + 1
        y, h = y + rows[0], rows[-1] - rows[0] + 1
    fx = store.w / sw
    fy = store.h / sh
    return (int(x * fx), int(y * fy), int(np.ceil(w * fx)), int(np.ceil(h * fy)))


def roi_feature(frame: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = roi
    crop = frame[y:y + h, x:x + w]
    fh = max(1, int(round(h * FEAT_W / w)))
    f = cv2.resize(crop, (FEAT_W, fh), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(f, (3, 3), 0)


def frac_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a, b) > PIX_DIFF))


def detect(store: FrameStore, roi: tuple[int, int, int, int], min_stable_s: float = 5.0,
           progress=None, cancel_check=None) -> tuple[list[DetectedSlide], float]:
    """Geeft (slides, aandeel_stabiel) terug. aandeel_stabiel = fractie van de opname
    die in een stabiel stuk valt (laag = beeld nooit stabiel)."""
    mm = store.open()
    n = store.n
    need = int(np.ceil(min_stable_s / store.interval)) + 1    # 2 s → 4 frames = 6 s
    runs: list[StableRun] = []
    run_start = 0
    prev = roi_feature(np.asarray(mm[0]), roi)
    feats_in_run = [prev]

    def close_run(first: int, last: int, feats: list[np.ndarray]):
        if last - first + 1 >= need:
            mid = (first + last) // 2
            runs.append(StableRun(first, last, mid, feats[(mid - first)]))

    for i in range(1, n):
        f = roi_feature(np.asarray(mm[i]), roi)
        if frac_diff(f, prev) < STATIC_FRAC:
            feats_in_run.append(f)
        else:
            close_run(run_start, i - 1, feats_in_run)
            run_start = i
            feats_in_run = [f]
        prev = f
        if progress and i % 100 == 0:
            progress(i / n)
        if cancel_check and i % 200 == 0:
            cancel_check()
    close_run(run_start, n - 1, feats_in_run)
    del mm

    stable_frames = sum(r.last - r.first + 1 for r in runs)
    stable_share = stable_frames / max(1, n)

    # opeenvolgende stabiele stukken met (bijna) hetzelfde beeld samenvoegen
    merged: list[StableRun] = []
    for r in runs:
        if merged and frac_diff(merged[-1].feat, r.feat) < SAME_FRAC:
            m = merged[-1]
            merged[-1] = StableRun(m.first, r.last, m.rep, m.feat)
        else:
            merged.append(r)

    slides: list[DetectedSlide] = []
    for k, r in enumerate(merged):
        # de slide begint waar het vorige stabiele stuk ophield (de wissel zelf)
        start_i = r.first if k == 0 else merged[k - 1].last + 1
        start = store.time(start_i) - store.interval / 2
        slides.append(DetectedSlide(start=max(store.t0 - store.interval / 2, start), end=0.0,
                                    rep_time=store.time(r.rep), rep_index=r.rep, feat=r.feat))
    end_of_video = store.time(n - 1) + store.interval / 2
    for k, s in enumerate(slides):
        s.end = slides[k + 1].start if k + 1 < len(slides) else end_of_video
    return slides, stable_share
