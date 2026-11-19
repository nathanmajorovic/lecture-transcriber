"""
De initial_prompt: eerst het vakprofiel, daarna de meest relevante termen uit
de slides-PDF, binnen de limiet van Whisper (223 tokens).

Let op: faster-whisper knipt een te lange prompt af door het BEGIN weg te
laten. Zonder eigen budgettering zou het vakprofiel dus als eerste sneuvelen.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from .config import Course

TOKEN_LIMIT = 220  # Whisper: n_text_ctx // 2 - 1 = 223; wat marge

_STOP = set("""
a an the and or of to in on for with by as at from is are was were be been this that these those it its
we you they he she i not no yes if then else when where which who what how why can could should would
will may might must do does did done have has had into over under than also only such so but all any
each every some more most less least other same very via per e.g i.e etc example examples figure table
slide slides page lecture chapter section part note notes see using use used given let thus hence
de het een en of in op voor met door als bij van uit is zijn was waren wordt worden werd dit dat deze
die er niet geen ja dan als waar wie wat hoe waarom kan kunnen moet moeten zal zullen ook enkel zo maar
alle elke sommige meer minder andere zelfde zeer via per bv vb voorbeeld voorbeelden figuur tabel les
hoofdstuk deel nota zie stelling bewijs definitie gevolg lemma oefening oplossing opgave dus stel
""".split())

_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9]*(?:[-'][A-Za-zÀ-ÿ0-9]+)*")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


MIN_SCORE = 1.5


def pdf_terms(pdf: Path, max_terms: int = 30,multi_token: Callable[[str], bool] | None = None
              ) -> list[str]:
    """Haal kandidaat-vaktermen uit een slides-PDF, gerangschikt op relevantie.

    Score = aantal pagina's waarop de term staat × gewicht. Extra gewicht voor
    acroniemen (GMRES), termen in slidetitels, woordparen die terugkomen
    ("kernel trick") en woorden die Whisper niet als één token kent
    (dat zijn typisch de zeldzame vaktermen die het verkeerd spelt).
    """
    import pymupdf

    pages_with: dict[str, set[int]] = defaultdict(set)
    in_title: Counter = Counter()
    display: dict[str, Counter] = defaultdict(Counter)

    def candidates(line: str) -> list[str]:
        """Woorden + woordparen. Een paar enkel als de twee woorden in dezelfde regel
        rechtstreeks naast elkaar staan (geen '=', ',', tabelcel, … ertussen)."""
        ms = list(_WORD.finditer(line))
        out = [m.group() for m in ms]
        for a, b in zip(ms, ms[1:]):
            gap = line[a.end():b.start()]
            if gap.strip() == "" and a.group().lower() not in _STOP and b.group().lower() not in _STOP:
                out.append(f"{a.group()} {b.group()}")
        return out

    with pymupdf.open(str(pdf)) as doc:
        for pno, page in enumerate(doc):
            d = page.get_text("dict")
            lines = []
            for b in d.get("blocks", []):
                for l in b.get("lines", []):
                    spans = [s for s in l.get("spans", []) if s.get("text", "").strip()]
                    if spans:
                        lines.append((max(s["size"] for s in spans),
                                      "".join(s["text"] for s in spans)))
            if not lines:
                continue
            max_size = max(sz for sz, _ in lines)
            for sz, text in lines:
                for w in candidates(text):
                    key = w.lower()
                    pages_with[key].add(pno)
                    display[key][w] += 1
                    if sz >= 0.9 * max_size:
                        in_title[key] += 1

    scores: dict[str, float] = {}
    for key, pages in pages_with.items():
        parts = key.split()
        if any(p in _STOP for p in parts) or any(len(p) < 2 for p in parts):
            continue
        if re.fullmatch(r"[\d\W]+", key) or re.fullmatch(r"[a-z]\d", key):
            continue   # getallen en labels als R1, x2
        form = display[key].most_common(1)[0][0]
        is_acronym = bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{1,9}s?", form))
        is_pair = len(parts) == 2
        if not (is_acronym or len(key) >= 5):
            continue
        if is_pair and len(pages) < 2:
            continue  # toevallig woordpaar
        w = 1.0
        if is_acronym:
            w *= 2.0
        if is_pair:
            w *= 1.6
        if form[0].isupper() and not is_acronym:
            w *= 1.3  # eigennaam: Arnoldi, Lanczos, Ritz
        if in_title[key]:
            w *= 1.8
        if multi_token and not is_pair:
            try:
                if multi_token(form):
                    w *= 1.5
                elif not is_acronym:
                    w *= 0.25  # gewoon woord dat Whisper zeker kent ("rules", "Illustration")
            except Exception:
                pass
        if is_pair and multi_token:
            try:   # paar van twee doodgewone woorden ("one rule") is weinig nuttig
                if not (multi_token(parts[0]) or multi_token(parts[1])):
                    w *= 0.5
            except Exception:
                pass
        scores[key] = len(pages) * w

    ranked = [k for k in sorted(scores, key=lambda k: -scores[k]) if scores[k] >= MIN_SCORE]
    chosen = ranked[: max_terms * 2]
    pair_words = {w for k in chosen if " " in k for w in k.split()}
    out: list[str] = []
    for key in chosen:
        # los woord dat al in een gekozen woordpaar zit: overbodig
        if " " not in key and key in pair_words:
            continue
        out.append(display[key].most_common(1)[0][0])
        if len(out) >= max_terms:
            break
    return out


def build_prompt(course: Course, extra_terms: list[str] | None,
                 count_tokens: Callable[[str], int] | None = None,
                 limit: int = TOKEN_LIMIT) -> tuple[str, list[str]]:
    """Bouw de prompt: intro + vakjargon, daarna PDF-termen zolang het budget het toelaat.
    Geeft (prompt, gebruikte_pdf_termen) terug."""
    count = count_tokens or (lambda s: len(s) // 3 + 1)  # ruwe schatting zonder tokenizer
    intro = _clean(course.intro)
    seen: set[str] = set()
    items: list[str] = []
    for j in course.jargon:
        if j.lower() not in seen:
            seen.add(j.lower())
            items.append(j)

    def render(xs: list[str]) -> str:
        body = ", ".join(xs)
        return _clean(f"{intro} {body}." if body else intro)

    # vakprofiel zelf ook inkorten als het te lang zou zijn
    while items and count(render(items)) > limit:
        items.pop()
    used: list[str] = []
    for t in extra_terms or []:
        if t.lower() in seen:
            continue
        trial = items + [t]
        if count(render(trial)) > limit:
            continue  # misschien past een kortere term nog
        items = trial
        used.append(t)
        seen.add(t.lower())
    return render(items), used
