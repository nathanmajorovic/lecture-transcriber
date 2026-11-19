"""Uitvoerbestanden: transcript.txt, info.txt, prompt_template.md (+ slidebestanden)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SlideSpan:
    index: int                    # volgnummer van de detectie (1, 2, 3, …)
    page: int | None              # PDF-pagina, None = onbekend/onzeker of geen PDF
    start: float
    end: float
    confidence: str               # zeker / waarschijnlijk / onzeker / "-" (geen PDF)
    score: float | None = None
    alternatives: list = field(default_factory=list)   # [(pagina, score), …]
    screenshot: str | None = None  # relatief pad t.o.v. de uitvoermap
    revisit: bool = False          # pagina werd eerder al getoond
    _hires: Path | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class PartResult:
    index: int                 # 1-gebaseerd
    source: Path
    duration: float            # duur van de (volledige) opname
    segments: list             # list[Segment], tijden t.o.v. het begin van de opname
    slides: list = field(default_factory=list)   # list[SlideSpan] (indien video)
    slides_note: str | None = None               # bv. "slidedetectie niet bruikbaar"


def fmt_ts(t: float, long: bool) -> str:
    t = max(0, int(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if long else f"{m:02d}:{s:02d}"


def fmt_dur(t: float) -> str:
    t = int(round(t))
    m, s = divmod(t, 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def part_header(p: PartResult, n_parts: int) -> str | None:
    return f"=== DEEL {p.index} ({p.source.name}) ===" if n_parts > 1 else None


def transcript_lines(p: PartResult) -> list[str]:
    long = p.duration >= 3600
    return [f"[{fmt_ts(s.start, long)}] {s.text}" for s in p.segments]


def write_transcript(path: Path, parts: list[PartResult]) -> None:
    out: list[str] = []
    for p in parts:
        h = part_header(p, len(parts))
        if h:
            if out:
                out.append("")
            out.append(h)
        out += transcript_lines(p)
    _atomic_write(path, "\n".join(out) + "\n")


def slide_header(sp: SlideSpan, long: bool, has_pdf: bool) -> str:
    times = f"{fmt_ts(sp.start, long)} – {fmt_ts(sp.end, long)}, {fmt_dur(sp.duration)}"
    if not has_pdf:
        shot = f" [{sp.screenshot}]" if sp.screenshot else ""
        return f"=== Beeld {sp.index} ({times}){shot} ==="
    if sp.page is None:
        hint = ", ".join(f"p. {p}" for p, _ in sp.alternatives[:2])
        shot = f"; zie {sp.screenshot}" if sp.screenshot else ""
        return f"=== Slide ? ({times}; onzeker – mogelijk {hint}{shot}) ==="
    mark = "?" if sp.confidence == "waarschijnlijk" else ""
    extra = []
    if sp.revisit:
        extra.append("opnieuw getoond")
    if sp.confidence == "waarschijnlijk":
        alt = [p for p, _ in sp.alternatives if p != sp.page][:1]
        extra.append("waarschijnlijk" + (f", anders p. {alt[0]}" if alt else ""))
    tail = f"; {'; '.join(extra)}" if extra else ""
    return f"=== Slide {sp.page}{mark} ({times}{tail}) ==="


def write_per_slide(path: Path, lesson_name: str, parts: list[PartResult], has_pdf: bool) -> None:
    out = [f"# Transcript per slide – {lesson_name}", ""]
    if has_pdf:
        out += ["Slidenummers = paginanummers van de PDF. `?` = waarschijnlijk (bijna-identieke "
                "pagina's), `Slide ?` = onzeker (geen goede overeenkomst; zie de schermafbeelding).",
                ""]
        # tijd per pagina (over alle delen en terugsprongen samen)
        tot: dict[int, float] = {}
        cnt: dict[int, int] = {}
        for p in parts:
            for s in p.slides:
                if s.page is not None:
                    tot[s.page] = tot.get(s.page, 0) + s.duration
                    cnt[s.page] = cnt.get(s.page, 0) + 1
        if tot:
            longest = sorted(tot, key=lambda k: -tot[k])[:3]
            out += ["## Tijd per slide", "", "| Slide | Tijd | Keer getoond |", "|---:|---:|---:|"]
            for pg in sorted(tot):
                star = " ⭐" if pg in longest else ""
                out.append(f"| {pg}{star} | {fmt_dur(tot[pg])} | {cnt[pg]} |")
            out += ["", "⭐ = de drie slides waar het langst bij stilgestaan werd.", ""]
    else:
        out += ["Geen slides-PDF: elk *beeld* is een gedetecteerde slidewissel, met een "
                "schermafbeelding in de map `slides/`.", ""]

    for p in parts:
        long = p.duration >= 3600
        h = part_header(p, len(parts))
        if h:
            out += [h, ""]
        if not p.slides:
            if p.slides_note:
                out += [f"> {p.slides_note}", ""]
            out += transcript_lines(p) + [""]
            continue
        spans = p.slides
        k = 0
        current = None
        for seg in p.segments:
            while k < len(spans) and seg.start >= spans[k].start - 0.5:
                current = spans[k]
                k += 1
                out += ["", slide_header(current, long, has_pdf)]
            if current is None:
                current = "pre"
                out += ["=== Vóór de eerste slide ==="]
            out.append(f"[{fmt_ts(seg.start, long)}] {seg.text}")
        # slides zonder gesproken tekst ook vermelden
        while k < len(spans):
            out += ["", slide_header(spans[k], long, has_pdf), "(geen spraak)"]
            k += 1
        out.append("")
    _atomic_write(path, "\n".join(out).replace("\n\n\n", "\n\n") + "\n")


def write_timeline(path: Path, parts: list[PartResult], has_pdf: bool) -> None:
    """CSV met ';' (zodat Excel met Belgische instellingen het meteen goed opent)."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["deel", "volgnummer", "slide", "start", "einde", "duur_s", "zekerheid", "score",
                "alternatieven", "schermafbeelding"])
    for p in parts:
        long = p.duration >= 3600
        for s in p.slides:
            w.writerow([p.index, s.index, s.page if s.page is not None else ("?" if has_pdf else ""),
                        fmt_ts(s.start, long), fmt_ts(s.end, long), int(round(s.duration)),
                        s.confidence, f"{s.score:.2f}".replace(".", ",") if s.score is not None else "",
                        " ".join(f"p{pg}" for pg, _ in s.alternatives[:3]), s.screenshot or ""])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8-sig")
    os.replace(tmp, path)


def write_info(path: Path, items: list[tuple[str, str]], warnings: list[str]) -> None:
    w = max(len(k) for k, _ in items)
    lines = [f"{k.ljust(w)} : {v}" for k, v in items]
    if warnings:
        lines += ["", "Waarschuwingen:"] + [f"- {x}" for x in warnings]
    _atomic_write(path, "\n".join(lines) + "\n")


def write_prompt_template(path: Path, course_title: str, language: str, has_slide_file: bool) -> None:
    taal = "Nederlands" if language == "nl" else "English" if language == "en" else language
    slide_hint = ("Gebruik `transcript_per_slide.md`: de kopjes geven aan welke slide op dat moment "
                  "in beeld was en hoe lang. Lang op een slide blijven wijst op belangrijke stof.\n"
                  if has_slide_file else
                  "Er is geen slide-indeling: koppel de uitleg zelf aan de juiste slide op basis "
                  "van de inhoud.\n")
    text = f"""# Instructie: slide-by-slide samenvatting – {course_title}

Je krijgt drie dingen:
1. de slides of cursusnota's van deze les (PDF);
2. een automatisch transcript van de lesopname (`transcript.txt`, regels `[mm:ss] tekst`);
3. eventueel `transcript_per_slide.md` en `slides_timeline.csv`.

{slide_hint}
## Wat ik wil
Maak een samenvatting **per slide**, in de volgorde van de slides. Per slide:
- 🎙️ **Wat de prof zegt**: de mondelinge uitleg uit het transcript die NIET letterlijk op de
  slide staat (intuïtie, voorbeelden, waarschuwingen, verbanden). Vermeld de tijdstempel.
- 📌 **Kern**: de definities, stellingen en formules van de slide, kort en correct.
- ⚠️ **Let op / examen**: wat de prof benadrukt ("dit moet je kennen", "typische fout",
  "dit komt op het examen"), en veelgemaakte fouten.
Slides waar de prof niets extra over zegt mag je kort houden.

## Belangrijk over het transcript
- Het transcript is automatisch gemaakt en **niet foutloos**. Vreemde woorden zijn
  waarschijnlijk **verkeerd verstaan jargon**: controleer altijd met de slides en gebruik de
  juiste term (bv. "kriel of deelruimte" = Krylov-deelruimte, "armoldi" = Arnoldi).
- Waar de prof op het bord schrijft ("dit hier", "naar die kant") is de audio onvolledig: vul aan
  met de slides en zeg expliciet dat je dat doet.
- Gebruik het transcript als **bron van uitleg**, niet als letterlijke tekst om te citeren.
- Verzin niets: als iets niet in de slides of het transcript staat, zeg dat.

## Opmaak
- Formules in **leesbare Unicode-notatie** (bv. ‖r‖₂, A⁻¹, ∑ᵢ, xᵀAx, λ₁ ≥ λ₂, ∈, ℝⁿ),
  **geen rauwe LaTeX**.
- Kopjes per slide: `## Slide 7 – <titel>`.
- Taal van de samenvatting: Nederlands (vaktermen mogen in het Engels blijven).
- Taal van de les: {taal}.

## Op het einde
- Een lijst met examendoelen (wat moet ik kunnen?).
- Enkele oefeningen of examenvragen in de stijl van de les, met korte oplossing.
"""
    _atomic_write(path, text)
