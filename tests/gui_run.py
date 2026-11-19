"""Echte run via de GUI (fragment van 1 minuut per les), met twee schermafbeeldingen.

    python tests\\gui_run.py OUTDIR SHOT_PREFIX opname1 [opname2 …] [slides.pdf]
"""
import ctypes
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
ctypes.windll.shcore.SetProcessDpiAwareness(1)

from lecture_transcriber import gui  # noqa: E402

out_dir, shot = Path(sys.argv[1]), sys.argv[2]
paths = [Path(p) for p in sys.argv[3:]]

# dialoogvensters niet laten blokkeren: alles loggen
for name in ("showinfo", "showwarning", "showerror"):
    setattr(gui.messagebox, name, lambda *a, _n=name, **k: print(f"[{_n}]", *a, flush=True))
gui.messagebox.askyesno = lambda *a, **k: (print("[askyesno]", *a, flush=True), True)[1]

root = gui.TkinterDnD.Tk()
app = gui.App(root)
app._add_paths([p for p in paths if p.suffix.lower() != ".pdf"])
pdfs = [p for p in paths if p.suffix.lower() == ".pdf"]
if pdfs:
    app.tree.selection_set(app.items[-1].iid)
    app._add_paths(pdfs)
app.out_mode.set("vast")
app.out_dir.set(str(out_dir))
app.clip_start.set("20:00")
app.clip_dur.set("1:00")
root.geometry(f"{int(1020 * app.scale)}x{int(800 * app.scale)}+20+20")
root.attributes("-topmost", True)


def screenshot(name):
    root.update_idletasks()
    x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    ps = (f"Add-Type -AssemblyName System.Drawing; Add-Type -TypeDefinition 'using System.Runtime.InteropServices;"
          f" public class D {{ [DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); }}';"
          f" [D]::SetProcessDPIAware() | Out-Null; $b = New-Object System.Drawing.Bitmap {w},{h};"
          f" [System.Drawing.Graphics]::FromImage($b).CopyFromScreen({x},{y},0,0,$b.Size);"
          f" $b.Save('{shot}_{name}.png')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps])
    print("screenshot", name, flush=True)


state = {"mid": False}


def watch():
    running = app.worker is not None and app.worker.is_alive()
    statuses = [it.status for it in app.items]
    if running and not state["mid"] and any(it.status == "transcriberen" and it.progress > 0.2
                                            for it in app.items):
        state["mid"] = True
        screenshot("tijdens")
    if app.worker is not None and not running and app.control is None:
        print("statussen:", statuses, flush=True)
        screenshot("klaar")
        root.after(500, root.destroy)
        return
    root.after(300, watch)


root.after(1500, app._start)
root.after(2000, watch)
root.mainloop()
