"""
Eén les verwerken (één of meer opnames → één uitvoermap), met:
- controles vooraf (ffmpeg, RAM/VRAM, schijf, batterij),
- hervatten vanaf de laatst afgewerkte chunk (_work/state.json),
- transcriptie, daarna (bij video) slidedetectie, daarna de uitvoerbestanden.

Wordt gebruikt door zowel de CLI als de GUI. Communicatie naar buiten via
`emit(kind, **data)`; vragen aan de gebruiker via `ask(titel, tekst) -> bool`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__
from . import audio as A
from . import device as dev
from .config import RunOptions
from .output import (PartResult, fmt_dur, fmt_ts, write_info, write_per_slide,
                     write_prompt_template, write_timeline, write_transcript)
from .prompt import build_prompt, pdf_terms
from .system import (RAM_COMFORT_GB, Cancelled, FriendlyError, disk_free_gb, find_ffmpeg,
                     model_ram_gb, resources, set_low_priority)
from .transcribe import Control, Segment, Transcriber

log = logging.getLogger("lecture")
Ask = Callable[[str, str], bool]

STATE_VERSION = 1
_PART_RE = re.compile(r"[\s_\-]*\b(?:deel|part|dl|pt)[\s_\-]*(\d+)\b", re.IGNORECASE)


# ------------------------------------------------------------------ lessen groeperen

@dataclass
class Lesson:
    name: str
    parts: list[Path]

    def out_dir(self, out_root: Path | None) -> Path:
        base = out_root or self.parts[0].parent
        return Path(base) / f"{self.name}_transcript"


def _split_part(stem: str) -> tuple[str, int | None]:
    m = _PART_RE.search(stem)
    if not m:
        return stem.strip(" _-"), None
    base = (stem[: m.start()] + stem[m.end():]).strip(" _-")
    return base, int(m.group(1))


def group_files(paths: list[Path], separate: bool = False) -> list[Lesson]:
    """Bestanden waarvan de naam enkel verschilt in 'DEEL 1' / 'DEEL 2' horen bij dezelfde les."""
    if separate:
        return [Lesson(p.stem, [p]) for p in paths]
    groups: dict[tuple[str, str], list[tuple[int, Path]]] = {}
    order: list[tuple[str, str]] = []
    for i, p in enumerate(paths):
        base, n = _split_part(p.stem)
        key = (str(p.parent).lower(), base.lower())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((n if n is not None else 10_000 + i, p))
    out = []
    for key in order:
        items = sorted(groups[key])
        name = _split_part(items[0][1].stem)[0] if len(items) > 1 else items[0][1].stem
        out.append(Lesson(name, [p for _, p in items]))
    return out


# ------------------------------------------------------------------ toestand (hervatten)

def _fingerprint(d: dict) -> str:
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


def _save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ logbestand

def _start_log(out_dir: Path) -> logging.Handler:
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(logs / f"run_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    return h


WORK_ROOT = Path(os.environ.get("LECTURE_WORK_DIR",
                                Path.home() / ".lecture-transcriber" / "work"))


def work_dir_for(out_dir: Path) -> Path:
    """Tijdelijke map BUITEN OneDrive (anders synchroniseert OneDrive honderden MB aan
    WAV- en framebestanden en houdt het mappen vast). Vast per uitvoermap → hervatten werkt."""
    h = hashlib.sha1(str(out_dir).lower().encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^\w\-]+", "_", out_dir.name)[:40]
    return WORK_ROOT / f"{safe}_{h}"


def _remove_tree(path: Path, tries: int = 5) -> None:
    """Op Windows houden OneDrive, de indexering of een virusscanner bestanden soms even vast."""
    import gc
    gc.collect()   # eventuele memmaps sluiten
    for i in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.5 * (i + 1))


def _slides_summary(results: list[PartResult], state: dict, opts: RunOptions) -> str:
    if not any(p["kind"] == "video" for p in state["parts"]):
        return "geen (enkel audio)"
    if not opts.detect_slides:
        return "uitgeschakeld"
    bits = []
    for r, p in zip(results, state["parts"]):
        if p["kind"] != "video":
            continue
        if r.slides:
            unc = sum(1 for s in r.slides if s.confidence == "onzeker")
            prob = sum(1 for s in r.slides if s.confidence == "waarschijnlijk")
            src = "gekoppeld aan PDF" if opts.slides_pdf else "zonder PDF (schermafbeeldingen)"
            bits.append(f"deel {r.index}: {len(r.slides)} slides, {src}"
                        + (f", {prob} waarschijnlijk" if prob else "")
                        + (f", {unc} onzeker" if unc else ""))
        else:
            bits.append(f"deel {r.index}: niet bruikbaar")
    return "; ".join(bits) or "-"


# ------------------------------------------------------------------ hoofdroutine

def run_lesson(lesson: Lesson, opts: RunOptions, emit, control: Control | None = None,
               ask: Ask | None = None, force: bool = False) -> Path:
    control = control or Control()
    ask = ask or (lambda title, text: True)
    out_dir = lesson.out_dir(opts.out_root).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = work_dir_for(out_dir)
    handler = _start_log(out_dir)
    warnings: list[str] = []

    def say(kind: str, **data):
        if kind == "warning":
            warnings.append(data["text"])
            log.warning(data["text"])
        elif kind in ("log", "status"):
            log.info(data["text"])
        emit(kind, **data)

    try:
        return _run(lesson, opts, say, control, ask, force, out_dir, work, warnings)
    except Cancelled:
        say("status", text="Geannuleerd. Afgewerkte stukken zijn bewaard; de volgende keer gaat "
                           "het verder waar het stopte.")
        raise
    except Exception:
        log.exception("Fout tijdens verwerking")
        raise
    finally:
        log.removeHandler(handler)
        handler.close()


def _run(lesson: Lesson, opts: RunOptions, say, control: Control, ask: Ask, force: bool,
         out_dir: Path, work: Path, warnings: list[str]) -> Path:
    t_start = time.time()
    set_low_priority()
    find_ffmpeg()

    finished = (not (work / "state.json").exists()) or (work / "DONE").exists()
    if (out_dir / "transcript.txt").is_file() and finished and not force:
        say("status", text=f"'{lesson.name}' is al verwerkt ({out_dir}). Gebruik --force om "
                           f"opnieuw te beginnen.")
        return out_dir

    # ---------------------------------------------------- 1. opnames onderzoeken
    infos = [A.probe(p) for p in lesson.parts]
    for inf in infos:
        say("log", text=f"{inf.path.name}: {inf.kind}, {fmt_dur(inf.duration)}"
                        + (f", {inf.width}×{inf.height}" if inf.has_video else ""))

    # ---------------------------------------------------- 2. middelen controleren
    choice = dev.choose_device(opts.model or opts.course.model, opts.force_cpu, opts.beam_size)
    for w in choice.warnings:
        say("warning", text=w)
    res = resources()
    say("log", text=res.describe())
    say("resources", resources=res)

    chunk_min = opts.chunk_min
    need = model_ram_gb(choice.device, choice.model)
    if res.ram_available_gb < RAM_COMFORT_GB:
        msg = (f"Er is maar {res.ram_available_gb:.1f} GB RAM vrij (aanbevolen: minstens "
               f"{RAM_COMFORT_GB:.0f} GB). Sluit eerst wat apps (browser, Teams, Discord, …) om te "
               f"vermijden dat het proces gestopt wordt.\n\nToch doorgaan? (met kleinere stukken "
               f"van 10 minuten)")
        if not opts.assume_yes and not ask("Weinig geheugen", msg):
            raise Cancelled()
        res = resources()   # misschien heeft de gebruiker intussen apps gesloten
        if res.ram_available_gb < RAM_COMFORT_GB:
            chunk_min = min(chunk_min, 10)
            say("warning", text=f"Weinig RAM ({res.ram_available_gb:.1f} GB vrij): stukken van "
                                f"{chunk_min:.0f} min.")
    if res.ram_available_gb < need:
        smaller = "small"
        msg = (f"Het model '{choice.model}' heeft bij het laden ±{need:.1f} GB RAM nodig, er is "
               f"{res.ram_available_gb:.1f} GB vrij.\n\nWil je het kleinere model '{smaller}' "
               f"gebruiken? (Nee = toch '{choice.model}' proberen)")
        if opts.assume_yes or ask("Te weinig geheugen voor dit model", msg):
            choice = dev.DeviceChoice(choice.device, choice.compute_type, smaller,
                                      choice.beam_size, choice.gpu_name)
            say("warning", text=f"Kleiner model gekozen wegens weinig RAM: {smaller}.")
    if res.on_battery:
        say("warning", text="De laptop draait op batterij: de GPU wordt dan afgeremd en alles "
                            "duurt langer. Steek de lader in voor de beste snelheid.")

    total_src = sum(i.duration for i in infos)
    clip = opts.clip_duration
    wav_bytes = sum((min(clip, i.duration) if clip else i.duration) * A.WAV_BYTES_PER_S for i in infos)
    need_gb = wav_bytes / 2**30 * 1.2 + 0.3
    if disk_free_gb(out_dir) < need_gb:
        raise FriendlyError(f"Te weinig schijfruimte: nodig ±{need_gb:.1f} GB, vrij "
                            f"{disk_free_gb(out_dir):.1f} GB op de schijf van {out_dir}.")

    # ---------------------------------------------------- 3. toestand / hervatten
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "state.json"
    requested = {
        "parts": [str(p) for p in lesson.parts], "clip": [opts.clip_start, opts.clip_duration],
        "language": opts.effective_language, "model": choice.model, "device": choice.device,
        "beam": choice.beam_size, "slides_pdf": str(opts.slides_pdf or ""),
        "course": opts.course.key, "chunk_min": chunk_min, "v": STATE_VERSION,
    }
    fp = _fingerprint(requested)
    state = _load_json(state_path)
    if state and state.get("fingerprint") != fp:
        if state.get("parts") and any(p.get("done") for p in state["parts"]):
            say("warning", text="De instellingen zijn gewijzigd sinds de vorige (onafgewerkte) run; "
                                "ik begin opnieuw met de nieuwe instellingen.")
        for f in work.glob("p*_c*.json"):
            f.unlink()
        state = None
    if state:
        n_done = sum(len(p["done"]) for p in state["parts"])
        if n_done:
            say("status", text=f"Hervatten: {n_done} stuk(ken) waren al klaar.")
    else:
        state = {"fingerprint": fp, "requested": requested, "language": None, "prompt": None,
                 "pdf_terms": None,
                 "parts": [{"src": str(i.path), "kind": i.kind, "src_duration": i.duration,
                            "wav": f"part{k + 1}.wav", "offset": opts.clip_start or 0.0,
                            "duration": None, "bounds": None, "done": []}
                           for k, i in enumerate(infos)]}
        _save_json(state_path, state)

    # ---------------------------------------------------- 4. audio omzetten (per deel)
    for k, part in enumerate(state["parts"]):
        wav = work / part["wav"]
        if not wav.is_file():
            say("status", text=f"Audio omzetten (deel {k + 1}/{len(state['parts'])}) …")
        A.extract_wav(Path(part["src"]), wav, opts.threads, opts.clip_start, opts.clip_duration,
                      cancel_check=control.check)
        if part["duration"] is None:
            part["duration"] = A.wav_duration(wav)
        if part["bounds"] is None:
            part["bounds"] = A.chunk_bounds(wav, part["duration"], chunk_min * 60)
        _save_json(state_path, state)

    # ---------------------------------------------------- 5. model, prompt, taal
    tr = Transcriber(choice, opts.threads, say)
    t_model = time.time()
    tr.load()
    say("device", choice=tr.choice)

    if state["prompt"] is None:
        terms: list[str] = []
        if opts.slides_pdf:
            try:
                terms = pdf_terms(opts.slides_pdf, multi_token=tr.is_multi_token)
            except Exception as e:
                say("warning", text=f"Kon geen termen uit de PDF halen ({e}); enkel het vakprofiel "
                                    f"wordt gebruikt.")
        prompt, used = build_prompt(opts.course, terms, tr.count_tokens)
        state["prompt"], state["pdf_terms"] = prompt, used
        _save_json(state_path, state)
    prompt = state["prompt"]
    say("log", text=f"Prompt ({tr.count_tokens(prompt)} tokens): {prompt}")
    if state["pdf_terms"]:
        say("log", text=f"Termen uit de PDF: {', '.join(state['pdf_terms'])}")

    lang = state["language"]
    if lang is None:
        if opts.effective_language == "auto":
            say("status", text="Taal detecteren op 3 fragmenten (25%, 50%, 75%) …")
            p0 = state["parts"][0]
            wav0 = work / p0["wav"]
            clips = [A.read_wav(wav0, max(0.0, f * p0["duration"] - 15), 30) for f in (.25, .5, .75)]
            lang, votes = tr.detect_language(clips)
            say("log", text="Taaldetectie: " + ", ".join(f"{l} ({p:.2f})" for l, p in votes)
                            + f" → {lang}")
        else:
            lang = opts.effective_language
        state["language"] = lang
        _save_json(state_path, state)

    # ---------------------------------------------------- 6. transcriberen, chunk per chunk
    total_audio = sum(p["duration"] for p in state["parts"])
    done_audio = sum(b - a for p in state["parts"] for i, (a, b) in enumerate(p["bounds"])
                     if i in p["done"])
    run_audio = 0.0          # in deze run verwerkte audio (voor de snelheid)
    t_run = time.time()
    preview_sent = done_audio > 0
    preview_lines: list[str] = []
    chunk_models: set[str] = set()

    for k, part in enumerate(state["parts"]):
        wav = work / part["wav"]
        long = part["src_duration"] >= 3600
        for ci, (a, b) in enumerate(part["bounds"]):
            if ci in part["done"]:
                continue
            control.check()
            say("status", text=f"Transcriberen: deel {k + 1}/{len(state['parts'])}, "
                               f"stuk {ci + 1}/{len(part['bounds'])} "
                               f"({fmt_ts(a + part['offset'], long)}–{fmt_ts(b + part['offset'], long)})")
            audio = A.read_wav(wav, a, b - a)
            base_done = done_audio

            def on_segment(seg: Segment, _a=a, _base=base_done, _long=long, _off=part["offset"]):
                nonlocal run_audio, preview_sent
                pos = _base + (seg.end - _a)
                elapsed = time.time() - t_run
                speed = (run_audio + seg.end - _a) / elapsed if elapsed > 1 else 0
                eta = (total_audio - pos) / speed if speed > 0 else None
                say("progress", done=pos, total=total_audio, eta=eta, speed=speed)
                if not preview_sent:
                    preview_lines.append(f"[{fmt_ts(seg.start + _off, _long)}] {seg.text}")
                    if seg.end - _a >= 60 or len(preview_lines) >= 12:
                        preview_sent = True
                        say("preview", language=lang, lines=list(preview_lines))

            segs = tr.transcribe(audio, lang, prompt, a, control, on_segment)
            del audio
            chunk_models.add(tr.choice.describe())
            _save_json(work / f"p{k + 1}_c{ci:02d}.json",
                       {"model": tr.choice.describe(),
                        "segments": [[s.start, s.end, s.text] for s in segs]})
            part["done"].append(ci)
            _save_json(state_path, state)
            done_audio += b - a
            run_audio += b - a
            say("progress", done=done_audio, total=total_audio,
                eta=None if done_audio >= total_audio else
                (total_audio - done_audio) / (run_audio / max(1e-6, time.time() - t_run)),
                speed=run_audio / max(1e-6, time.time() - t_run))
            say("log", text=f"Deel {k + 1}, stuk {ci + 1} klaar ({len(segs)} segmenten).")
        if not preview_sent and preview_lines:
            preview_sent = True
            say("preview", language=lang, lines=list(preview_lines))

    final_choice = tr.choice
    tr.unload()     # geheugen vrijgeven vóór de slidedetectie
    t_transcribe = time.time() - t_model

    # ---------------------------------------------------- 7. resultaten verzamelen
    results: list[PartResult] = []
    for k, part in enumerate(state["parts"]):
        segs: list[Segment] = []
        for ci in range(len(part["bounds"])):
            data = _load_json(work / f"p{k + 1}_c{ci:02d}.json") or {"segments": []}
            segs += [Segment(s + part["offset"], e + part["offset"], t) for s, e, t in data["segments"]]
        results.append(PartResult(k + 1, Path(part["src"]), part["src_duration"], segs))

    # ---------------------------------------------------- 8. slidedetectie (enkel video)
    has_slides = False
    if opts.detect_slides and any(p["kind"] == "video" for p in state["parts"]):
        try:
            from .slides import run_slides
        except ImportError:
            run_slides = None
        if run_slides is not None:
            control.check()
            has_slides = run_slides(results, state, opts, out_dir, work, say, control)

    # ---------------------------------------------------- 9. uitvoer
    write_transcript(out_dir / "transcript.txt", results)
    has_pdf = opts.slides_pdf is not None
    if has_slides:
        write_per_slide(out_dir / "transcript_per_slide.md", lesson.name, results, has_pdf)
        write_timeline(out_dir / "slides_timeline.csv", results, has_pdf)
    write_prompt_template(out_dir / "prompt_template.md", opts.course.title, lang, has_slides)
    elapsed = time.time() - t_start
    items = [
        ("Les", lesson.name),
        ("Opnames", "; ".join(f"{p.name}" for p in lesson.parts)),
        ("Duur audio", fmt_dur(total_audio) + (f" (fragment vanaf {fmt_ts(opts.clip_start or 0, False)})"
                                                 if opts.clip_duration else "")),
        ("Vak", f"{opts.course.title} ({opts.course.key})"),
        ("Taal", lang + (" (automatisch gedetecteerd)" if opts.effective_language == "auto" else "")),
        ("Model", "; ".join(sorted(chunk_models)) or final_choice.describe()),
        ("Instellingen", f"vad_filter=True, condition_on_previous_text=False, "
                         f"threads={opts.threads}, stukken van ±{chunk_min:.0f} min"),
        ("Prompt", state["prompt"]),
        ("PDF-termen", ", ".join(state["pdf_terms"] or []) or "-"),
        ("Slides", _slides_summary(results, state, opts)),
        ("Verwerkingstijd", f"{fmt_dur(elapsed)} (transcriptie {fmt_dur(t_transcribe)}, "
                            f"{total_audio / max(1, t_transcribe):.1f}× realtime)"),
        ("Gemaakt", f"{datetime.now():%Y-%m-%d %H:%M} met lecture-transcriber {__version__}"),
    ]
    write_info(out_dir / "info.txt", items, warnings)

    (work / "DONE").write_text("klaar", encoding="utf-8")
    if not opts.keep_temp:
        _remove_tree(work)
        if work.exists():
            say("log", text=f"Tijdelijke map kon niet volledig verwijderd worden: {work}")
    say("done", out_dir=out_dir, elapsed=elapsed,
        lines=sum(len(r.segments) for r in results))
    return out_dir
