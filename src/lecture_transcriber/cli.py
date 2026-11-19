"""
Commandoregel.

    lecture-transcribe opname.mp4 --course ml --slides ml_les3.pdf
    lecture-transcribe "les 7 DEEL_1.m4a" "les 7 DEEL_2.m4a" --course wiskunde-ai
    lecture-transcribe opname.mp4 --course ml --clip 20:00 2:00      # test op 2 minuten
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from .config import RunOptions, get_course, load_courses   # noqa: E402
from .job import group_files, run_lesson                    # noqa: E402
from .output import fmt_dur, fmt_ts                         # noqa: E402
from .system import Cancelled, FriendlyError, default_threads   # noqa: E402
from .transcribe import Control                             # noqa: E402


def parse_time(s: str) -> float:
    """'90', '1:30' of '1:02:03' → seconden."""
    parts = [float(x) for x in s.split(":")]
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def parse_crop(s: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(v) for v in s.replace(" ", "").split(","))
        return x, y, w, h
    except ValueError:
        raise argparse.ArgumentTypeError("gebruik --crop x,y,breedte,hoogte (bv. 0,0,960,540)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="lecture-transcribe",
        description="Lesopnames (video of audio) lokaal omzetten naar een transcript met tijdstempels.")
    ap.add_argument("inputs", nargs="*", type=Path, help="opname(s); DEEL_1/DEEL_2 worden samengevoegd")
    ap.add_argument("--course", "-c", help="vakprofiel uit courses.yaml (bv. ml, wiskunde-ai)")
    ap.add_argument("--slides", type=Path, help="slides-PDF van deze les")
    ap.add_argument("--language", "-l", help="nl, en, … of auto (standaard: uit het vakprofiel)")
    ap.add_argument("--model", "-m", help="large-v3-turbo (standaard op GPU), large-v3, medium, small, …")
    ap.add_argument("--beam", type=int, help="beam_size (standaard 5 op GPU, 1 op CPU)")
    ap.add_argument("--cpu", action="store_true", help="GPU niet gebruiken")
    ap.add_argument("--chunk-min", type=float, default=20, help="stukgrootte in minuten (standaard 20)")
    ap.add_argument("--threads", type=int, default=default_threads(),
                    help=f"CPU-threads (standaard {default_threads()} = helft van de kernen)")
    ap.add_argument("--out", type=Path, help="vaste uitvoermap (standaard: naast de opname)")
    ap.add_argument("--separate", action="store_true", help="elk bestand apart, ook DEEL_1/DEEL_2")
    ap.add_argument("--clip", nargs=2, metavar=("START", "DUUR"),
                    help="enkel een stuk verwerken, bv. --clip 20:00 2:00 (om te testen)")
    ap.add_argument("--crop", type=parse_crop, help="slidegebied in het beeld: x,y,breedte,hoogte")
    ap.add_argument("--no-slides", action="store_true", help="geen slidedetectie")
    ap.add_argument("--keep-temp", action="store_true", help="tijdelijke bestanden (_work) bewaren")
    ap.add_argument("--force", action="store_true", help="opnieuw verwerken, ook als het al klaar is")
    ap.add_argument("--yes", "-y", action="store_true", help="niet vragen bij weinig geheugen")
    ap.add_argument("--list-courses", action="store_true", help="toon de vakprofielen en stop")
    return ap


class ConsoleUI:
    def __init__(self) -> None:
        self._progress_shown = False

    def _end_progress(self):
        if self._progress_shown:
            print()
            self._progress_shown = False

    def emit(self, kind: str, **d) -> None:
        if kind == "progress":
            pct = 100 * d["done"] / max(1e-6, d["total"])
            eta = f", nog ±{fmt_dur(d['eta'])}" if d.get("eta") else ""
            speed = f", {d['speed']:.1f}× realtime" if d.get("speed") else ""
            print(f"\r  {pct:5.1f}%  ({fmt_ts(d['done'], d['total'] >= 3600)} / "
                  f"{fmt_ts(d['total'], d['total'] >= 3600)}{speed}{eta})      ", end="", flush=True)
            self._progress_shown = True
            return
        if kind == "slides_progress":
            stage = {"frames": "beelden ophalen", "detect": "wissels zoeken",
                     "grab": "slides in volle resolutie"}.get(d["stage"], d["stage"])
            print(f"\r  Slides – {stage}: {100 * d['fraction']:5.1f}%      ", end="", flush=True)
            self._progress_shown = True
            return
        self._end_progress()
        if kind == "status":
            print(f"» {d['text']}")
        elif kind == "warning":
            print(f"⚠ {d['text']}")
        elif kind == "log":
            print(f"  {d['text']}")
        elif kind == "device":
            print(f"» Toestel: {d['choice'].describe()}")
        elif kind == "preview":
            print(f"\n── Eerste minuut (taal: {d['language']}) – klopt de opname en de taal? ──")
            for line in d["lines"]:
                print("   " + line)
            print("── (Ctrl+C om te stoppen; de volgende keer gaat het verder) ──\n")
        elif kind == "done":
            print(f"\n✔ Klaar in {fmt_dur(d['elapsed'])}: {d['lines']} regels → {d['out_dir']}")

    @staticmethod
    def ask(title: str, text: str) -> bool:
        print(f"\n{title}\n{text}")
        if not sys.stdin or not sys.stdin.isatty():
            print("(geen invoer mogelijk → nee)")
            return False
        return input("[j/n] > ").strip().lower() in ("j", "ja", "y", "yes")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        if args.list_courses:
            for k, c in load_courses().items():
                print(f"{k:15s} {c.title} [{c.language}] – {len(c.jargon)} termen")
            return 0
        if not args.inputs:
            build_parser().print_help()
            return 1
        course = get_course(args.course)
        if args.slides and not args.slides.is_file():
            raise FriendlyError(f"Slides-PDF niet gevonden: {args.slides}")
        clip = (parse_time(args.clip[0]), parse_time(args.clip[1])) if args.clip else (None, None)
        opts = RunOptions(course=course, slides_pdf=args.slides, language=args.language,
                          model=args.model, beam_size=args.beam, force_cpu=args.cpu,
                          chunk_min=args.chunk_min, threads=args.threads, out_root=args.out,
                          clip_start=clip[0], clip_duration=clip[1], keep_temp=args.keep_temp,
                          crop=args.crop, detect_slides=not args.no_slides, assume_yes=args.yes)
        lessons = group_files([p.resolve() for p in args.inputs], args.separate)
        ui = ConsoleUI()
        control = Control()
        for i, lesson in enumerate(lessons, 1):
            print(f"\n=== Les {i}/{len(lessons)}: {lesson.name} ({len(lesson.parts)} opname(s)) ===")
            run_lesson(lesson, opts, ui.emit, control, ui.ask, force=args.force)
        return 0
    except (Cancelled, KeyboardInterrupt):
        print("\nGestopt. Start hetzelfde commando opnieuw om verder te gaan waar het stopte.")
        return 130
    except FriendlyError as e:
        print(f"\n✖ {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
