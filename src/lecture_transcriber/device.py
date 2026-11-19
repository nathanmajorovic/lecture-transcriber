"""
Kies het rekentoestel (NVIDIA-GPU via CUDA, anders CPU) en het bijhorende model.

Op Windows zitten cuBLAS en cuDNN in de pip-pakketten nvidia-cublas-cu12 en
nvidia-cudnn-cu12. Hun DLL-mappen moeten vindbaar zijn *voordat* ctranslate2
(de motor onder faster-whisper) ze probeert te laden; dat regelt
`register_cuda_dlls()`.

De Intel Iris Xe ondersteunt geen CUDA, dus elk CUDA-toestel is per definitie
de NVIDIA-kaart. We controleren de naam toch via nvidia-smi en loggen die.
"""
from __future__ import annotations

import os
import shutil
import site
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_DLLS_REGISTERED = False


def register_cuda_dlls() -> list[str]:
    """Maak de DLL's uit de nvidia-*-pip-pakketten vindbaar. Geeft de toegevoegde mappen terug."""
    global _DLLS_REGISTERED
    added: list[str] = []
    if sys.platform != "win32":
        return added
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        nv = Path(root) / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            d = str(bin_dir)
            if d in added:
                continue
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            # ctranslate2 laadt sommige DLL's via de klassieke zoekvolgorde → ook PATH
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            added.append(d)
    _DLLS_REGISTERED = True
    return added


def nvidia_smi() -> dict | None:
    """Naam, totaal en vrij VRAM (MiB) van de eerste NVIDIA-kaart, of None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    name, total, free, driver = [x.strip() for x in out[0].split(",")]
    return {"name": name, "vram_total_mb": int(total), "vram_free_mb": int(free), "driver": driver}


@dataclass
class DeviceChoice:
    device: str                  # "cuda" of "cpu"
    compute_type: str            # bv. "int8_float16" of "int8"
    model: str                   # bv. "large-v3-turbo" of "small"
    beam_size: int
    gpu_name: str | None = None
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        where = f"GPU ({self.gpu_name})" if self.device == "cuda" else "CPU"
        return f"{self.model} op {where}, {self.compute_type}, beam {self.beam_size}"


GPU_DEFAULT_MODEL = "large-v3-turbo"
GPU_COMPUTE = "int8_float16"
CPU_DEFAULT_MODEL = "small"
CPU_COMPUTE = "int8"


def cuda_usable() -> tuple[bool, str]:
    """(bruikbaar, uitleg). Kijkt of ctranslate2 een CUDA-toestel ziet en int8_float16 aankan."""
    register_cuda_dlls()
    try:
        import ctranslate2
    except Exception as e:  # pragma: no cover
        return False, f"ctranslate2 kon niet geladen worden: {e}"
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:
        return False, f"CUDA-controle mislukt: {e}"
    if n < 1:
        return False, "geen CUDA-toestel gevonden (NVIDIA-driver of CUDA-bibliotheken ontbreken)"
    types = ctranslate2.get_supported_compute_types("cuda")
    if GPU_COMPUTE not in types:
        return False, f"GPU ondersteunt {GPU_COMPUTE} niet (wel: {sorted(types)})"
    return True, f"{n} CUDA-toestel(len), compute types: {sorted(types)}"


def choose_device(requested_model: str | None = None, force_cpu: bool = False,
                  beam_size: int | None = None) -> DeviceChoice:
    warnings: list[str] = []
    if not force_cpu:
        ok, why = cuda_usable()
        if ok:
            info = nvidia_smi()
            gpu = info["name"] if info else "NVIDIA (naam onbekend)"
            model = requested_model or GPU_DEFAULT_MODEL
            if model == "large-v3":
                warnings.append("large-v3 op 4 GB VRAM is krap; bij geheugentekort val ik terug op "
                                "large-v3-turbo.")
            return DeviceChoice("cuda", GPU_COMPUTE, model, beam_size or 5, gpu, warnings)
        warnings.append(f"CUDA werkt niet, ik gebruik de CPU (trager). Reden: {why}")
    model = requested_model or CPU_DEFAULT_MODEL
    if model.startswith("large"):
        warnings.append(f"{model} op CPU is erg traag; overweeg 'small'.")
    return DeviceChoice("cpu", CPU_COMPUTE, model, beam_size or 1, None, warnings)


def is_cuda_oom(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda_error_out_of_memory" in msg or "cudaerrormemoryallocation" in msg
