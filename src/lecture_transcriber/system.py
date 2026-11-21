"""
Alles wat met het systeem te maken heeft: ffmpeg vinden, lage prioriteit,
threads, geheugen, schijfruimte en batterij. Foutmeldingen in gewone taal.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil


class FriendlyError(Exception):
    """Fout met een boodschap die rechtstreeks aan de gebruiker getoond kan worden."""


class Cancelled(Exception):
    """De gebruiker heeft geannuleerd."""


# ---------------------------------------------------------------- ffmpeg

_FFMPEG: dict[str, str] | None = None


def _user_path_dirs() -> list[str]:
    """PATH zoals het in het register staat (winget past dat aan, maar een al
    lopend proces ziet de nieuwe waarde niet)."""
    dirs: list[str] = []
    if sys.platform != "win32":
        return dirs
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    dirs += [os.path.expandvars(d) for d in val.split(";") if d]
            except OSError:
                pass
    except ImportError:
        pass
    return dirs


def find_ffmpeg() -> dict[str, str]:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    found: dict[str, str] = {}
    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        if not p:
            for d in _user_path_dirs():
                cand = Path(d) / f"{exe}.exe"
                if cand.is_file():
                    p = str(cand)
                    break
        if not p and sys.platform == "win32":
            pattern = os.path.expandvars(
                rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\{exe}.exe")
            hits = glob.glob(pattern)
            p = hits[0] if hits else None
        if not p and sys.platform != "win32":
            # Een app die vanuit Finder start, ziet het PATH van Homebrew soms niet
            for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
                cand = Path(d) / exe
                if cand.is_file():
                    p = str(cand)
                    break
        if not p:
            how = {"win32": "winget install Gyan.FFmpeg",
                   "darwin": "brew install ffmpeg"}.get(sys.platform, "sudo apt install ffmpeg")
            raise FriendlyError(
                f"{exe} niet gevonden. Installeer het met:  {how}\n"
                "en start de app daarna opnieuw.")
        found[exe] = p
    _FFMPEG = found
    return found


# ---------------------------------------------------------------- prioriteit

def set_low_priority() -> None:
    """Zet het eigen proces op BELOW_NORMAL, zodat de pc bruikbaar blijft."""
    try:
        p = psutil.Process()
        if sys.platform == "win32":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except (psutil.Error, OSError):
        pass


def subprocess_kwargs() -> dict:
    """Extra argumenten voor subprocess: lage prioriteit en geen zwart venster."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW}
    return {"preexec_fn": lambda: os.nice(10)}  # pragma: no cover


def open_folder(path: Path | str) -> None:
    """Open een map in Verkenner (Windows), Finder (macOS) of de bestandsbeheerder (Linux)."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def default_threads() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


# ---------------------------------------------------------------- geheugen / schijf / batterij

@dataclass
class Resources:
    ram_available_gb: float
    ram_total_gb: float
    ram_percent: float
    vram_free_mb: int | None
    vram_total_mb: int | None
    gpu_name: str | None
    on_battery: bool | None
    battery_percent: float | None

    def describe(self) -> str:
        s = (f"RAM: {self.ram_available_gb:.1f} GB vrij van {self.ram_total_gb:.1f} GB "
             f"({self.ram_percent:.0f}% in gebruik)")
        if self.vram_free_mb is not None:
            s += f" | VRAM: {self.vram_free_mb / 1024:.1f} GB vrij van {self.vram_total_mb / 1024:.1f} GB"
        if self.on_battery:
            s += f" | op batterij ({self.battery_percent:.0f}%)"
        return s


def resources() -> Resources:
    from .device import nvidia_smi
    vm = psutil.virtual_memory()
    smi = nvidia_smi()
    try:
        bat = psutil.sensors_battery()
    except Exception:
        bat = None
    return Resources(
        ram_available_gb=vm.available / 2**30,
        ram_total_gb=vm.total / 2**30,
        ram_percent=vm.percent,
        vram_free_mb=smi["vram_free_mb"] if smi else None,
        vram_total_mb=smi["vram_total_mb"] if smi else None,
        gpu_name=smi["name"] if smi else None,
        on_battery=(not bat.power_plugged) if bat else None,
        battery_percent=bat.percent if bat else None,
    )


# Ruwe schatting van het piek-RAM tijdens het laden van een model (GB).
MODEL_RAM_GB = {
    ("cuda", "large-v3-turbo"): 1.5, ("cuda", "large-v3"): 2.5,
    ("cuda", "medium"): 1.2, ("cuda", "small"): 0.6,
    ("cpu", "large-v3-turbo"): 1.8, ("cpu", "large-v3"): 3.0,
    ("cpu", "medium"): 1.5, ("cpu", "small"): 0.8, ("cpu", "base"): 0.4, ("cpu", "tiny"): 0.3,
    # Apple Silicon: gedeeld geheugen, het model staat dus volledig in het RAM (float16)
    ("mlx", "large-v3-turbo"): 2.0, ("mlx", "large-v3"): 3.5,
    ("mlx", "medium"): 1.8, ("mlx", "small"): 0.7,
}
RAM_COMFORT_GB = 3.0


def model_ram_gb(device: str, model: str) -> float:
    return MODEL_RAM_GB.get((device, model), 1.5)


def disk_free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 2**30
