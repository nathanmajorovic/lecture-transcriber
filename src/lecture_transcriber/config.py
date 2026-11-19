"""Vakprofielen (courses.yaml) en run-instellingen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .system import FriendlyError, default_threads

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COURSES_FILE = Path(os.environ.get("LECTURE_COURSES", PROJECT_ROOT / "courses.yaml"))


@dataclass
class Course:
    key: str
    title: str
    language: str = "auto"           # nl / en / ... / auto
    model: str | None = None
    intro: str = ""
    jargon: list[str] = field(default_factory=list)


def load_courses(path: Path = COURSES_FILE) -> dict[str, Course]:
    if not Path(path).is_file():
        raise FriendlyError(f"Vakprofielen niet gevonden: {path}")
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise FriendlyError(f"courses.yaml bevat een fout (let op inspringing en dubbele punten):\n{e}")
    out: dict[str, Course] = {}
    for key, c in raw.items():
        c = c or {}
        out[key] = Course(
            key=key,
            title=c.get("title") or key,
            language=str(c.get("language") or "auto"),
            model=c.get("model") or None,
            intro=c.get("intro") or "",
            jargon=[str(j) for j in (c.get("jargon") or [])],
        )
    return out


_HEADER = """# Vakprofielen. Kies het vak in de app of met --course <naam>.
# language: nl / en / auto   (auto = detectie op 25/50/75% van de opname)
# model: leeg laten = automatisch (large-v3-turbo op GPU, small op CPU)
# jargon: termen die vooraan in de initial_prompt komen (maximaal ±224 tokens in totaal)
"""


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):   # lijstjes ingesprongen, zoals met de hand
        return super().increase_indent(flow, False)


def slugify(title: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "vak"


def save_course(course: Course, path: Path = COURSES_FILE) -> None:
    """Voegt een vak toe aan (of werkt het bij in) courses.yaml. Andere vakken blijven ongewijzigd."""
    path = Path(path)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}
    raw[course.key] = {"title": course.title, "language": course.language, "model": course.model,
                       "intro": course.intro, "jargon": list(course.jargon)}
    blocks = [yaml.dump({k: v}, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                        default_flow_style=False, width=1000).replace(": null\n", ":\n")
              for k, v in raw.items()]
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(_HEADER + "\n" + "\n".join(blocks), encoding="utf-8")
    tmp.replace(path)


def get_course(key: str | None) -> Course:
    if not key:
        return Course(key="(geen)", title="Algemeen", language="auto")
    courses = load_courses()
    if key not in courses:
        raise FriendlyError(f"Onbekend vak '{key}'. Beschikbaar: {', '.join(courses)}.\n"
                            f"Voeg het toe in {COURSES_FILE}.")
    return courses[key]


@dataclass
class RunOptions:
    course: Course
    slides_pdf: Path | None = None
    language: str | None = None       # overschrijft course.language
    model: str | None = None          # overschrijft course.model / automatische keuze
    beam_size: int | None = None      # None = 5 op GPU, 1 op CPU
    force_cpu: bool = False
    chunk_min: float = 20.0
    threads: int = field(default_factory=default_threads)
    out_root: Path | None = None      # vaste uitvoermap; None = naast de opname
    clip_start: float | None = None   # enkel voor tests: stuk uit de opname
    clip_duration: float | None = None
    keep_temp: bool = False
    crop: tuple[int, int, int, int] | None = None   # x, y, w, h (in pixels van de video)
    detect_slides: bool = True
    assume_yes: bool = False          # CLI: niet vragen bij weinig RAM

    @property
    def effective_language(self) -> str:
        return (self.language or self.course.language or "auto").lower()
