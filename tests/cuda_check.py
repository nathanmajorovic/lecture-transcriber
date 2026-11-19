"""
Controle: werkt faster-whisper op de NVIDIA-GPU?

    python tests\\cuda_check.py                 # test large-v3-turbo / int8_float16
    python tests\\cuda_check.py --model tiny    # snelle test zonder grote download
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lecture_transcriber import device  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--model", default=device.GPU_DEFAULT_MODEL)
args = ap.parse_args()

print("CUDA-DLL-mappen:", *device.register_cuda_dlls(), sep="\n  ")
ok, why = device.cuda_usable()
print(f"\nCUDA bruikbaar: {ok} – {why}")
smi = device.nvidia_smi()
print("nvidia-smi:", smi)
if not ok:
    sys.exit(1)

import numpy as np  # noqa: E402
import psutil  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

vram_before = smi["vram_free_mb"] if smi else None
print(f"\nRAM vrij: {psutil.virtual_memory().available / 2**30:.1f} GB")
t0 = time.time()
model = WhisperModel(args.model, device="cuda", compute_type=device.GPU_COMPUTE)
print(f"Model {args.model} geladen op GPU in {time.time() - t0:.1f} s")

# 20 s synthetisch signaal: genoeg om encoder (cuDNN-convoluties) en decoder (cuBLAS) te laten draaien
sr = 16000
t = np.arange(20 * sr) / sr
audio = (0.1 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 0.5 * t) > 0)).astype(np.float32)
t0 = time.time()
segs, info = model.transcribe(audio, language="en", beam_size=5, vad_filter=False,
                              condition_on_previous_text=False)
segs = list(segs)
dt = time.time() - t0
print(f"Transcriptie van 20 s audio in {dt:.1f} s ({20 / dt:.0f}× realtime), {len(segs)} segment(en)")

smi2 = device.nvidia_smi()
if smi2 and vram_before is not None:
    print(f"VRAM in gebruik door model: ±{vram_before - smi2['vram_free_mb']} MiB "
          f"(nog {smi2['vram_free_mb']} MiB vrij van {smi2['vram_total_mb']})")
print("\nOK: faster-whisper draait op", smi["name"] if smi else "de GPU")
