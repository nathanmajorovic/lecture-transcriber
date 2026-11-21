"""
Backend voor Macs met Apple Silicon (M1/M2/M3/…): mlx-whisper, dat de GPU van de Mac
gebruikt. faster-whisper (ctranslate2) kan die GPU niet gebruiken en zou op een Mac
enkel op de CPU draaien.

Verschillen met faster-whisper die hier worden opgevangen:
- mlx-whisper heeft geen ingebouwde VAD. We gebruiken de Silero-VAD van faster-whisper
  (dat ook op de Mac geïnstalleerd is) om stiltes weg te knippen, en zetten de
  tijdstempels daarna terug naar de tijd in de opname.
- mlx-whisper verwerkt een stuk audio in één blokkerende oproep. Om pauzeren en
  annuleren mogelijk te houden, verwerken we de spraak in porties van ±5 minuten.
- mlx-whisper ondersteunt geen beam search: altijd greedy (beam 1).

Opgelet: deze code is nog niet op echte Apple-hardware getest.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterator

import numpy as np

from .audio import SR

log = logging.getLogger("lecture")

REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

BATCH_S = 300          # maximaal ±5 min spraak per oproep (pauzeren/annuleren tussendoor)
MIN_SILENCE_MS = 700   # zelfde instelling als bij faster-whisper


def repo_for(model: str) -> str:
    """Modelnaam → Hugging Face-repo. Een volledige repo-naam ('org/naam') mag ook."""
    return REPOS.get(model, model)


def available() -> tuple[bool, str]:
    try:
        import mlx_whisper  # noqa: F401
    except Exception as e:
        return False, f"mlx-whisper kon niet geladen worden ({e})"
    return True, "mlx-whisper (GPU van de Mac)"


# ------------------------------------------------------------------ VAD

def speech_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    """Stukken met spraak, in samples. Lukt de VAD niet, dan de volledige audio."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=MIN_SILENCE_MS))
        return [(int(t["start"]), int(t["end"])) for t in ts]
    except Exception as e:
        log.warning("VAD niet beschikbaar (%s): ik verwerk de volledige audio.", e)
        return [(0, len(audio))]


def batches(regions: list[tuple[int, int]], max_s: float = BATCH_S) -> list[list[tuple[int, int]]]:
    """Groepeer spraakstukken in porties van hoogstens max_s seconden spraak."""
    out: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    cur_len = 0
    limit = int(max_s * SR)
    for a, b in regions:
        # een heel lang stuk zelf ook opdelen
        while b - a > limit:
            if cur:
                out.append(cur)
                cur, cur_len = [], 0
            out.append([(a, a + limit)])
            a += limit
        if cur and cur_len + (b - a) > limit:
            out.append(cur)
            cur, cur_len = [], 0
        cur.append((a, b))
        cur_len += b - a
    if cur:
        out.append(cur)
    return out


def to_original_time(t: float, pieces: list[tuple[int, int]], is_end: bool = False) -> float:
    """Tijd (s) in de aaneengeplakte spraak → tijd (s) in de oorspronkelijke audio.
    Een eindtijd die precies op een grens valt, hoort bij het stuk ervoor."""
    pos = t * SR
    cum = 0
    for a, b in pieces:
        n = b - a
        if pos < cum + n or (is_end and pos <= cum + n):
            return (a + max(0.0, pos - cum)) / SR
        cum += n
    return pieces[-1][1] / SR if pieces else t


# ------------------------------------------------------------------ model

class MlxModel:
    def __init__(self, model: str, emit: Callable[..., None]):
        self.model = model
        self.repo = repo_for(model)
        self._tok = None
        self._ensure_downloaded(emit)
        self._warmup()

    def _ensure_downloaded(self, emit) -> None:
        from huggingface_hub import snapshot_download
        try:
            snapshot_download(repo_id=self.repo, local_files_only=True)
        except Exception:
            emit("status", text=f"Model '{self.model}' wordt eenmalig gedownload "
                                f"(internet nodig, kan enkele minuten duren) …")
            snapshot_download(repo_id=self.repo)

    def _warmup(self) -> None:
        """Laadt het model in het geheugen (mlx-whisper houdt het daarna bij)."""
        import mlx_whisper
        mlx_whisper.transcribe(np.zeros(SR, dtype=np.float32), path_or_hf_repo=self.repo,
                               language="en", verbose=None)

    def unload(self) -> None:
        try:
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.model = None
            ModelHolder.model_path = None
        except Exception:
            pass

    # -------------------------------------------------- tokens (voor de initial_prompt)

    def _tokenizer(self):
        if self._tok is None:
            from mlx_whisper.tokenizer import get_tokenizer
            self._tok = get_tokenizer(multilingual=True, num_languages=100)
        return self._tok

    def count_tokens(self, text: str) -> int:
        try:
            return len(self._tokenizer().encode(" " + text.strip()))
        except Exception:
            return len(text) // 3 + 1

    def is_multi_token(self, word: str) -> bool:
        try:
            return len(self._tokenizer().encode(" " + word)) >= 2
        except Exception:
            return len(word) > 8

    # -------------------------------------------------- taal en transcriptie

    def detect_language(self, clip: np.ndarray) -> tuple[str, float]:
        import mlx_whisper
        r = mlx_whisper.transcribe(clip.astype(np.float32), path_or_hf_repo=self.repo,
                                   language=None, verbose=None,
                                   condition_on_previous_text=False)
        return r.get("language") or "en", 1.0

    def transcribe(self, audio: np.ndarray, language: str, prompt: str,
                   check: Callable[[], None]) -> Iterator[tuple[float, float, str]]:
        """Geeft (start, einde, tekst) in seconden t.o.v. het begin van `audio`."""
        import mlx_whisper
        for pieces in batches(speech_regions(audio)):
            check()
            speech = np.concatenate([audio[a:b] for a, b in pieces]).astype(np.float32)
            if len(speech) < SR // 2:
                continue
            r = mlx_whisper.transcribe(speech, path_or_hf_repo=self.repo, language=language,
                                       initial_prompt=prompt or None, verbose=None,
                                       condition_on_previous_text=False)
            for s in r.get("segments", []):
                check()
                yield (to_original_time(float(s["start"]), pieces),
                       to_original_time(float(s["end"]), pieces, is_end=True),
                       str(s.get("text", "")))
