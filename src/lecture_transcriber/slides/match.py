"""
Stabiele videobeelden koppelen aan pagina's van de slides-PDF.

Score = genormaliseerde kruiscorrelatie (NCC) tussen het (lichtjes vervaagde)
videobeeld en de gerenderde PDF-pagina, op dezelfde kleine resolutie. Dat is
het structuurdeel van SSIM, maar robuuster tegen helderheids- en
contrastverschillen van de opname.

Daarna een kleine volgorde-voorkeur (Viterbi): de prof gaat meestal naar de
volgende slide, maar mag terugspringen als het beeld daar duidelijk beter past.
Dat helpt bij bijna-identieke pagina's (bv. dezelfde tabel met een ander
gemarkeerd rijtje). Lage overeenkomst → "onzeker" in plaats van een gok.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ZEKER = 0.60
ONZEKER = 0.45
NCC_MIN = 0.45
MATCH_W = 400          # breedte waarop beeld en pagina vergeleken worden
MARGIN = 0.03
BONUS_NEXT = 0.04
PENALTY_BACK = 0.04
PENALTY_SKIP = 0.01


@dataclass
class Match:
    page: int | None             # 1-gebaseerd paginanummer, None = onzeker
    score: float
    confidence: str              # zeker / waarschijnlijk / onzeker
    top: list[tuple[int, float]] = field(default_factory=list)   # beste kandidaten (pagina, score)


def _prep(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    f = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(f, (3, 3), 0)


def render_pdf(pdf: Path, size: tuple[int, int]) -> list[np.ndarray]:
    """Render elke pagina in grijswaarden op `size` = (breedte, hoogte)."""
    import pymupdf
    out = []
    with pymupdf.open(str(pdf)) as doc:
        for page in doc:
            zoom = max(1.0, size[0] * 4 / page.rect.width)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY,
                                  alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            out.append(_prep(img, size))
    return out


def _norm(a: np.ndarray) -> np.ndarray:
    x = a.astype(np.float32).ravel()
    x -= x.mean()
    n = np.linalg.norm(x)
    return x / n if n > 1e-6 else x


def _ink(img: np.ndarray) -> np.ndarray:
    """'Inkt' = donkerte t.o.v. de achtergrond, na contrastnormalisatie (0..1).
    Zo maakt het niet uit dat de opname flets of overbelicht is."""
    x = img.astype(np.float32)
    lo, hi = np.percentile(x, 2), np.percentile(x, 98)
    x = np.clip((x - lo) / max(1.0, hi - lo), 0, 1)
    # inkt = afwijking van de achtergrondkleur (de meest voorkomende waarde); werkt dus
    # ook voor donkere slides met lichte tekst
    hist = np.histogram(x, bins=32, range=(0, 1))[0]
    bg = (np.argmax(hist) + 0.5) / 32
    ink = np.abs(x - bg)
    ink = np.clip(ink / max(0.25, float(np.percentile(ink, 99.5))), 0, 1)
    return cv2.GaussianBlur(ink, (5, 5), 1.5)


def valid_mask(size: tuple[int, int], W: np.ndarray | None) -> np.ndarray:
    """Gebied van de pagina dat de video werkelijk toont (rand eraf)."""
    w, h = size
    m = np.ones((h, w), np.float32)
    if W is not None:
        m = cv2.warpAffine(m, W, size, flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    m = cv2.erode(m, np.ones((7, 7), np.uint8))
    mx, my = int(0.015 * w), int(0.015 * h)
    m[:my], m[-my:], m[:, :mx], m[:, -mx:] = 0, 0, 0, 0
    return m


def score_matrix(feats: list[np.ndarray], pages: list[np.ndarray],
                 mask: np.ndarray | None = None) -> np.ndarray:
    """Asymmetrische inkt-overlap.

    precision = welk deel van wat IN BEELD staat, staat ook op de pagina
                (moet hoog zijn, ook als een animatie nog niet volledig opgebouwd is)
    recall    = welk deel van de PAGINA is al zichtbaar (weegt minder door animaties)
    Gemeenschappelijke elementen (balk, logo, …) worden eerst afgetrokken."""
    size = (pages[0].shape[1], pages[0].shape[0])
    if mask is None:
        mask = valid_mask(size, None)
    P = np.stack([_ink(p) for p in pages])
    common = np.percentile(P, 25, axis=0) if len(pages) >= 4 else np.zeros_like(P[0])
    P = np.maximum(P - common, 0) * mask
    k = np.ones((5, 5), np.uint8)
    Pd = np.stack([cv2.dilate(p, k) for p in P])             # tolerantie voor kleine verschuiving
    S = np.zeros((len(feats), len(pages)), np.float32)
    for i, f in enumerate(feats):
        f = _prep(f, size) if f.shape != pages[0].shape else f
        F = np.maximum(_ink(f) - common, 0) * mask
        Fd = cv2.dilate(F, k)
        fs = F.sum() + 1e-6
        for j in range(len(pages)):
            prec = np.minimum(F, Pd[j]).sum() / fs
            rec = np.minimum(Fd, P[j]).sum() / (P[j].sum() + 1e-6)
            # animaties: het beeld kan een deel van de pagina zijn, of omgekeerd (de PDF
            # bevat soms enkel de eerste opbouwstap) → de hoogste van de twee weegt het meest
            S[i, j] = 0.6 * max(prec, rec) + 0.4 * min(prec, rec)
    return S


def estimate_alignment(feats: list[np.ndarray], pages: list[np.ndarray], S: np.ndarray,
                       n_best: int = 6) -> np.ndarray | None:
    """Eén affiene transformatie video → PDF voor de hele opname (het slidegebied ligt vast),
    geschat met ECC op de slides die het duidelijkst bij één pagina horen."""
    if S.shape[1] < 2:
        return None
    srt = np.sort(S, axis=1)
    margin = srt[:, -1] - srt[:, -2]
    order = np.argsort(-margin)[:n_best]
    warps = []
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    for i in order:
        j = int(np.argmax(S[i]))
        tpl = cv2.GaussianBlur(pages[j].astype(np.float32), (5, 5), 0)
        img = cv2.GaussianBlur(feats[i].astype(np.float32), (5, 5), 0)
        w = np.eye(2, 3, dtype=np.float32)
        try:
            cc, w = cv2.findTransformECC(tpl, img, w, cv2.MOTION_AFFINE, crit, None, 5)
        except cv2.error:
            continue
        if cc > 0.5:
            warps.append(w)
    if len(warps) < 2:
        return None
    W = np.median(np.stack(warps), axis=0)
    # onrealistische vervormingen negeren
    sx, sy = np.hypot(W[0, 0], W[1, 0]), np.hypot(W[0, 1], W[1, 1])
    if not (0.7 < sx < 1.4 and 0.7 < sy < 1.4):
        return None
    return W


def apply_alignment(f: np.ndarray, W: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpAffine(f, W, size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


REFINE_WINDOW = 0.15  # kandidaten binnen deze afstand van de beste worden herbekeken
REFINE_WEIGHT = 0.6


def refine(S: np.ndarray, feats: list[np.ndarray], pages: list[np.ndarray],
           valid: np.ndarray | None = None) -> np.ndarray:
    """Tweede ronde voor bijna-identieke kandidaten: vergelijk enkel de pixels waar
    die kandidaten onderling verschillen (paginanummer, gemarkeerd rijtje, '3/8', …).
    Inkt die in beeld staat maar niet op de pagina weegt zwaarder dan omgekeerd
    (een animatie kan nog niet volledig opgebouwd zijn)."""
    size = (pages[0].shape[1], pages[0].shape[0])
    if valid is None:
        valid = valid_mask(size, None)
    P = [_ink(p) * valid for p in pages]
    k = np.ones((5, 5), np.uint8)
    S2 = S.copy()
    for i, f in enumerate(feats):
        row = S[i]
        cands = [j for j in np.argsort(-row)[:5] if row[j] >= row.max() - REFINE_WINDOW]
        if len(cands) < 2:
            continue
        F = _ink(_prep(f, size) if f.shape != pages[0].shape else f) * valid
        stack = np.stack([P[j] for j in cands])
        mask = (stack.max(0) - stack.min(0)) > 0.25
        mask = cv2.dilate(mask.astype(np.uint8), k).astype(bool)
        if mask.sum() < 20:
            continue
        Fd = cv2.dilate(F, k)
        pen = []
        for j in cands:
            Pd = cv2.dilate(P[j], k)
            extra = np.maximum(F - Pd, 0)[mask].sum()      # in beeld, niet op pagina
            missing = np.maximum(P[j] - Fd, 0)[mask].sum()  # op pagina, niet in beeld
            # bij een animatie is één van beide ~0 (beeld ⊂ pagina of pagina ⊂ beeld);
            # bij een verkeerde pagina zijn beide groot
            pen.append(min(extra, missing) + 0.25 * max(extra, missing))
        pen = np.array(pen, np.float32)
        norm = max(1.0, float(np.sqrt(mask.sum())) * 4)
        adj = -(pen - pen.min()) / norm                      # 0 voor de beste, negatief voor de rest
        for j, a in zip(cands, adj):
            S2[i, j] = row.max() + REFINE_WEIGHT * max(-0.3, float(a))
    return S2


def match_slides(feats: list[np.ndarray], pages: list[np.ndarray]) -> list[Match]:
    """`feats` = grijswaardenbeelden van het slidegebied (liefst in hoge resolutie),
    op dezelfde grootte als `pages`."""
    size = (pages[0].shape[1], pages[0].shape[0])
    S = score_matrix(feats, pages)
    W = estimate_alignment(feats, pages, S)
    valid = valid_mask(size, W)
    if W is not None:
        feats = [apply_alignment(f, W, size) for f in feats]
        S = score_matrix(feats, pages, valid)
    S = refine(S, feats, pages, valid)
    n, P = S.shape
    # Viterbi met volgorde-voorkeur
    trans = np.zeros((P, P), np.float32)
    for p in range(P):
        for q in range(P):
            if q == p + 1:
                trans[p, q] = BONUS_NEXT
            elif q < p:
                trans[p, q] = -PENALTY_BACK
            elif q > p + 1:
                trans[p, q] = -PENALTY_SKIP * min(q - p - 1, 5)
    best = S[0].copy()
    back = np.zeros((n, P), np.int32)
    for i in range(1, n):
        cand = best[:, None] + trans            # (vorige, volgende)
        back[i] = np.argmax(cand, axis=0)
        best = cand[back[i], np.arange(P)] + S[i]
    path = [int(np.argmax(best))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i, path[-1]]))
    path.reverse()

    size = (pages[0].shape[1], pages[0].shape[0])
    out: list[Match] = []
    for i, p in enumerate(path):
        row = S[i]
        order = np.argsort(-row)
        top = [(int(j) + 1, float(row[j])) for j in order[:3]]
        sc = float(row[p])
        others = np.delete(row, p)
        margin = sc - float(others.max()) if len(others) else 1.0
        # tweede, onafhankelijke controle: lijkt het beeld als geheel op de pagina?
        # (vangt bv. een browservenster of het bord op dat toevallig 'inkt' deelt)
        f = feats[i] if feats[i].shape == pages[0].shape else _prep(feats[i], size)
        ncc = float(_norm(f) @ _norm(pages[p]))
        if sc < ONZEKER or ncc < NCC_MIN:
            out.append(Match(None, sc, "onzeker", top))
            continue
        conf = "zeker" if sc >= ZEKER and margin >= MARGIN else "waarschijnlijk"
        out.append(Match(p + 1, sc, conf, top))
    return out
