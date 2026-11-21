"""
Spraak naar tekst met faster-whisper (of mlx-whisper op een Mac met Apple Silicon,
zie mlx_backend.py).

- Het model wordt één keer geladen; bij een fallback wordt het oude eerst vrijgegeven
  (nooit twee modellen tegelijk in het geheugen).
- CUDA out-of-memory → terugvallen op large-v3-turbo/int8_float16 en de chunk opnieuw doen;
  lukt ook dat niet → CPU 'small'.
- Taal 'auto' → detectie op 3 fragmenten (25/50/75%), meerderheid wint.
- Pauzeren/annuleren gebeurt tussen segmenten via `Control`.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import device as dev
from .audio import SR
from .system import Cancelled, FriendlyError

log = logging.getLogger("lecture")

Emit = Callable[..., None]   # emit(kind, **data)


class Control:
    """Gedeeld tussen GUI en werkthread: pauzeren, hervatten, annuleren."""

    def __init__(self) -> None:
        self._run = threading.Event()
        self._run.set()
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._run.clear()

    def resume(self) -> None:
        self._run.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        """Roep regelmatig op: blokkeert zolang gepauzeerd, gooit Cancelled bij annuleren."""
        if self._cancel.is_set():
            raise Cancelled()
        if not self._run.is_set():
            self._run.wait()
            if self._cancel.is_set():
                raise Cancelled()


@dataclass
class Segment:
    start: float
    end: float
    text: str


class Transcriber:
    def __init__(self, choice: dev.DeviceChoice, threads: int, emit: Emit):
        self.choice = choice
        self.threads = threads
        self.emit = emit
        self.model = None

    # ------------------------------------------------------------ model beheer

    @property
    def is_mlx(self) -> bool:
        return self.choice.device == "mlx"

    def _load_mlx(self) -> None:
        from .mlx_backend import MlxModel
        c = self.choice
        t0 = time.time()
        try:
            self.model = MlxModel(c.model, self.emit)
        except Exception as e:
            self.emit("warning", text=f"Het model kon niet op de GPU van de Mac geladen worden "
                                      f"({e}). Ik val terug op de CPU (trager).")
            self.choice = dev.choose_device(force_cpu=True)
            return self.load()
        self.emit("log", text=f"Model geladen in {time.time() - t0:.1f} s: {c.describe()}")

    def load(self) -> None:
        self.unload()
        c = self.choice
        self.emit("status", text=f"Model laden: {c.describe()} …")
        if self.is_mlx:
            return self._load_mlx()
        from faster_whisper import WhisperModel
        t0 = time.time()
        try:
            try:
                # eerst enkel lokaal: na de eerste download draait alles offline
                self.model = WhisperModel(c.model, device=c.device, compute_type=c.compute_type,
                                          cpu_threads=self.threads, num_workers=1,
                                          local_files_only=True)
            except Exception as e_local:
                if dev.is_cuda_oom(e_local) or "cuda" in str(e_local).lower() \
                        or "cublas" in str(e_local).lower() or "cudnn" in str(e_local).lower():
                    raise
                self.emit("status", text=f"Model '{c.model}' wordt eenmalig gedownload "
                                         f"(internet nodig, kan enkele minuten duren) …")
                self.model = WhisperModel(c.model, device=c.device, compute_type=c.compute_type,
                                          cpu_threads=self.threads, num_workers=1)
        except Exception as e:
            if c.device == "cuda":
                if dev.is_cuda_oom(e) and c.model != dev.GPU_DEFAULT_MODEL:
                    self.emit("warning", text=f"{c.model} past niet in het VRAM; ik neem "
                                              f"{dev.GPU_DEFAULT_MODEL}.")
                    self.choice = dev.DeviceChoice("cuda", dev.GPU_COMPUTE, dev.GPU_DEFAULT_MODEL,
                                                   c.beam_size, c.gpu_name)
                else:
                    self.emit("warning", text=f"Het model kon niet op de GPU geladen worden ({e}). "
                                              f"Ik val terug op de CPU (trager).")
                    self.choice = dev.choose_device(force_cpu=True)
                return self.load()
            raise FriendlyError(f"Het model '{c.model}' kon niet geladen worden: {e}\n"
                                "Bij de eerste keer moet het gedownload worden: controleer je "
                                "internetverbinding.")
        self.emit("log", text=f"Model geladen in {time.time() - t0:.1f} s: {self.choice.describe()}")

    def unload(self) -> None:
        if self.model is not None:
            if hasattr(self.model, "unload"):
                self.model.unload()
            self.model = None
            gc.collect()

    def fallback_after_oom(self) -> None:
        c = self.choice
        if c.device == "cuda" and c.model != dev.GPU_DEFAULT_MODEL:
            self.emit("warning", text=f"Geheugentekort op de GPU met {c.model}: ik schakel over op "
                                      f"{dev.GPU_DEFAULT_MODEL} ({dev.GPU_COMPUTE}) en herdoe dit stuk.")
            self.choice = dev.DeviceChoice("cuda", dev.GPU_COMPUTE, dev.GPU_DEFAULT_MODEL,
                                           c.beam_size, c.gpu_name)
        elif c.device == "cuda" and c.beam_size > 1:
            self.emit("warning", text="Geheugentekort op de GPU: ik verlaag beam_size naar 1 en "
                                      "herdoe dit stuk.")
            self.choice = dev.DeviceChoice(c.device, c.compute_type, c.model, 1, c.gpu_name)
            return  # model hoeft niet herladen te worden
        else:
            self.emit("warning", text="Geheugentekort op de GPU: ik schakel over op de CPU "
                                      "(model 'small') en herdoe dit stuk.")
            self.choice = dev.choose_device(force_cpu=True)
        self.load()

    # ------------------------------------------------------------ hulpfuncties

    def count_tokens(self, text: str) -> int:
        if self.is_mlx and self.model is not None:
            return self.model.count_tokens(text)
        tok = getattr(self.model, "hf_tokenizer", None)
        if tok is None:
            return len(text) // 3 + 1
        return len(tok.encode(" " + text.strip(), add_special_tokens=False).ids)

    def is_multi_token(self, word: str) -> bool:
        if self.is_mlx and self.model is not None:
            return self.model.is_multi_token(word)
        tok = getattr(self.model, "hf_tokenizer", None)
        if tok is None:
            return len(word) > 8
        return len(tok.encode(" " + word, add_special_tokens=False).ids) >= 2

    def detect_language(self, clips: list[np.ndarray]) -> tuple[str, list[tuple[str, float]]]:
        """Meerderheid over de fragmenten; bij gelijkstand de hoogste opgetelde zekerheid."""
        votes: list[tuple[str, float]] = []
        for clip in clips:
            if len(clip) < SR * 3:
                continue
            try:
                if self.is_mlx:
                    lang, prob = self.model.detect_language(clip)
                    votes.append((lang, float(prob)))
                    continue
                lang, prob, _ = self.model.detect_language(clip, vad_filter=True)
            except TypeError:  # oudere faster-whisper
                lang, prob, _ = self.model.detect_language(clip)
            except Exception as e:
                log.warning("taaldetectie op fragment mislukt: %s", e)
                continue
            votes.append((lang, float(prob)))
        if not votes:
            raise FriendlyError("Taaldetectie mislukt (geen spraak gevonden in de fragmenten). "
                                "Kies de taal zelf.")
        cnt = Counter(l for l, _ in votes)
        best = max(cnt, key=lambda l: (cnt[l], sum(p for x, p in votes if x == l)))
        return best, votes

    # ------------------------------------------------------------ transcriptie

    def transcribe(self, audio: np.ndarray, language: str, prompt: str, offset: float,
                   control: Control, on_segment: Callable[[Segment], None] | None = None
                   ) -> list[Segment]:
        """Transcribeer één chunk. Tijden in de uitvoer = offset + tijd in de chunk.
        Bij CUDA-geheugentekort: fallback en de volledige chunk opnieuw."""
        if self.is_mlx:
            out_mlx: list[Segment] = []
            for start, end, text in self.model.transcribe(audio, language, prompt, control.check):
                text = text.strip()
                if not text:
                    continue
                seg = Segment(offset + start, offset + end, text)
                out_mlx.append(seg)
                if on_segment:
                    on_segment(seg)
            return out_mlx
        while True:
            out: list[Segment] = []
            try:
                segs, _ = self.model.transcribe(
                    audio, language=language, beam_size=self.choice.beam_size,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 700},
                    condition_on_previous_text=False, initial_prompt=prompt or None)
                for s in segs:   # generator: het echte werk gebeurt hier
                    control.check()
                    text = s.text.strip()
                    if not text:
                        continue
                    seg = Segment(offset + s.start, offset + s.end, text)
                    out.append(seg)
                    if on_segment:
                        on_segment(seg)
                return out
            except Cancelled:
                raise
            except Exception as e:
                if self.choice.device == "cuda" and dev.is_cuda_oom(e):
                    self.emit("chunk_restart")
                    self.fallback_after_oom()
                    continue
                raise
