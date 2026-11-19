"""
Debug van de slidedetectie op één video (zonder transcriptie).

    python tests\\slides_debug.py video.mp4 [slides.pdf] [--out map] [--crop x,y,w,h]

Frames blijven in --out bewaard, zodat een tweede run meteen gaat.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lecture_transcriber.output import fmt_dur, fmt_ts  # noqa: E402
from lecture_transcriber.slides import analyze_video  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("video", type=Path)
ap.add_argument("pdf", type=Path, nargs="?")
ap.add_argument("--out", type=Path, default=Path("test_output/slides_dev"))
ap.add_argument("--crop")
args = ap.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None


def say(kind, **d):
    if kind in ("status", "log", "warning"):
        print(("⚠ " if kind == "warning" else "") + d["text"])


t0 = time.time()
va = analyze_video(args.video, args.out, "p1", args.out / "slides", args.pdf, threads=4,
                   crop=crop, say=say, rel_to=args.out)
print(f"klaar in {time.time() - t0:.0f} s")
if va.note:
    print("NOTE:", va.note)
for s in va.spans:
    alt = ", ".join(f"p{p}:{sc:.2f}" for p, sc in s.alternatives[:3])
    label = f"p{s.page}" if s.page else ("?" if args.pdf else f"#{s.index}")
    print(f"{fmt_ts(s.start, False)}–{fmt_ts(s.end, False)} ({fmt_dur(s.duration):>12}) → "
          f"{label:<4} {s.confidence:14s} [{alt}] {s.screenshot or ''}")
