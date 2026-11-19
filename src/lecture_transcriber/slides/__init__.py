"""
Slidedetectie voor video-opnames.

    frames.py  → 1 frame per 2 s, grijswaarden, lage resolutie (memmap op schijf)
    detect.py  → slidegebied + stabiele stukken (≥ 5 s) = slidewissels
    match.py   → koppeling aan de PDF-pagina's (indien PDF)

Per gedetecteerde slide wordt één frame in volle resolutie opgehaald: om te
vergelijken met de PDF en als schermafbeelding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..audio import probe
from ..output import SlideSpan
from . import detect as D
from . import frames as FR
from . import match as M

MIN_STABLE_SHARE = 0.30


def _read_png(path: Path) -> np.ndarray | None:
    """cv2.imread faalt op Windows-paden met speciale tekens; daarom via numpy."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except OSError:
        return None


def _shot_name(k: int, t: float) -> str:
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    stamp = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
    return f"slide_{k:03d}_at_{stamp}.png"


@dataclass
class VideoAnalysis:
    spans: list[SlideSpan] = field(default_factory=list)
    note: str | None = None
    roi_source: tuple[int, int, int, int] | None = None
    stable_share: float = 0.0


def analyze_video(video: Path, work: Path, tag: str, shots_dir: Path, pdf: Path | None,
                  threads: int, start: float | None = None, clip: float | None = None,
                  crop: tuple[int, int, int, int] | None = None, say=None, control=None,
                  rel_to: Path | None = None) -> VideoAnalysis:
    say = say or (lambda *a, **k: None)
    check = control.check if control else None
    info = probe(video)
    sw, sh = info.width, info.height

    say("status", text=f"Slides: beelden uit de video halen ({video.name}) …")
    store = FR.extract_frames(video, work, tag, sw, sh, info.duration, threads, start=start,
                              clip=clip, progress=lambda f: say("slides_progress", stage="frames",
                                                                fraction=f),
                              cancel_check=check)
    scale = sw / store.w

    if crop:
        x, y, w, h = crop
        roi = (int(x / scale), int(y / scale), max(8, int(w / scale)), max(8, int(h / scale)))
        say("log", text=f"Slidegebied (zelf ingesteld): {crop}")
    else:
        roi = D.find_roi(store)
        if roi is None:
            return VideoAnalysis(note="Geen duidelijk slidegebied gevonden in het beeld (bv. een "
                                      "camera die de zaal filmt). Slidedetectie was niet bruikbaar; "
                                      "het transcript is wel volledig. Tip: stel een uitsnede in "
                                      "met --crop x,y,breedte,hoogte.")
    roi_src = (int(roi[0] * scale), int(roi[1] * scale), int(roi[2] * scale), int(roi[3] * scale))
    if not crop:
        say("log", text=f"Slidegebied automatisch gevonden: x={roi_src[0]}, y={roi_src[1]}, "
                        f"{roi_src[2]}×{roi_src[3]} px (van {sw}×{sh})")

    slides, share = D.detect(store, roi, progress=lambda f: say("slides_progress", stage="detect",
                                                                fraction=f), cancel_check=check)
    say("log", text=f"{len(slides)} stabiele beelden, {share:.0%} van de tijd stabiel")
    if not slides or share < MIN_STABLE_SHARE:
        return VideoAnalysis(note=f"Het beeld was zelden stabiel ({share:.0%} van de tijd), "
                                  f"bv. door een bewegende camera. Slidedetectie was niet "
                                  f"bruikbaar; het transcript is wel volledig.",
                             roi_source=roi_src, stable_share=share)

    # één frame per slide in volle resolutie (kleur), bijgesneden tot het slidegebied
    hi_dir = work / f"{tag}_hires"
    hi_dir.mkdir(parents=True, exist_ok=True)
    hi_gray: list[np.ndarray] = []
    hi_paths: list[Path | None] = []
    say("status", text=f"Slides: {len(slides)} beelden in volle resolutie ophalen …")
    for k, s in enumerate(slides, 1):
        if check:
            check()
        png = hi_dir / f"{k:03d}.png"
        ok = png.is_file() or FR.grab_color_frame(video, s.rep_time, png, roi_src)
        img = _read_png(png) if ok else None
        if img is None:
            hi_gray.append(cv2.resize(s.feat, (M.MATCH_W, int(M.MATCH_W * s.feat.shape[0] /
                                                                  s.feat.shape[1]))))
            hi_paths.append(None)
        else:
            hi_gray.append(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
            hi_paths.append(png)
        say("slides_progress", stage="grab", fraction=k / len(slides))

    matches: list[M.Match | None] = [None] * len(slides)
    if pdf:
        h0, w0 = hi_gray[0].shape
        size = (M.MATCH_W, int(round(M.MATCH_W * h0 / w0)))
        pages = M.render_pdf(pdf, size)
        feats = [cv2.resize(g, size, interpolation=cv2.INTER_AREA) for g in hi_gray]
        matches = M.match_slides(feats, pages)

    # spans opbouwen; opeenvolgende stukken met dezelfde PDF-pagina samenvoegen (animaties)
    spans: list[SlideSpan] = []
    for k, (s, m) in enumerate(zip(slides, matches)):
        page = m.page if m else None
        if pdf and spans and page is not None and spans[-1].page == page:
            prev = spans[-1]
            prev.end = s.end
            order = {"zeker": 0, "waarschijnlijk": 1, "onzeker": 2}
            if order[m.confidence] < order[prev.confidence]:
                prev.confidence, prev.score = m.confidence, m.score
            continue
        spans.append(SlideSpan(
            index=len(spans) + 1, page=page, start=s.start, end=s.end,
            confidence=(m.confidence if m else "-"), score=(m.score if m else None),
            alternatives=(m.top if m else []), _hires=hi_paths[k]))

    # schermafbeeldingen: zonder PDF voor elke slide, met PDF enkel voor de onzekere
    pages_seen: set[int] = set()
    for sp in spans:
        if sp.page is not None:
            sp.revisit = sp.page in pages_seen
            pages_seen.add(sp.page)
        if (pdf is None or sp.confidence == "onzeker") and sp._hires is not None:
            shots_dir.mkdir(parents=True, exist_ok=True)
            dst = shots_dir / _shot_name(sp.index, sp.start)
            data = np.fromfile(str(sp._hires), dtype=np.uint8)
            data.tofile(str(dst))
            sp.screenshot = dst.relative_to(rel_to).as_posix() if rel_to else str(dst)
    return VideoAnalysis(spans=spans, roi_source=roi_src, stable_share=share)


def run_slides(results, state, opts, out_dir: Path, work: Path, say, control) -> bool:
    """Wordt aangeroepen door job.py. Vult results[k].slides / .slides_note in."""
    any_slides = False
    shots = out_dir / "slides"
    for k, (res, part) in enumerate(zip(results, state["parts"])):
        if part["kind"] != "video":
            continue
        try:
            va = analyze_video(Path(part["src"]), work, f"p{k + 1}", shots, opts.slides_pdf,
                               opts.threads, start=opts.clip_start, clip=opts.clip_duration,
                               crop=opts.crop, say=say, control=control, rel_to=out_dir)
        except Exception as e:
            from ..system import Cancelled
            if isinstance(e, Cancelled):
                raise
            say("warning", text=f"Slidedetectie mislukt voor deel {k + 1} ({e}); het transcript "
                                f"is wel volledig.")
            continue
        if va.note:
            res.slides_note = va.note
            say("warning", text=f"Deel {k + 1}: {va.note}")
            continue
        res.slides = va.spans
        any_slides = True
        n_uns = sum(1 for s in va.spans if s.confidence == "onzeker")
        say("log", text=f"Deel {k + 1}: {len(va.spans)} slides"
                        + (f", waarvan {n_uns} onzeker (zie map slides/)" if n_uns else ""))
    return any_slides
