"""Opent de GUI met testbestanden in de wachtrij en sluit na enkele seconden.
Schrijft de schermpositie van het venster naar stdout (voor een schermafbeelding)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lecture_transcriber import gui  # noqa: E402

paths = [Path(p) for p in sys.argv[1:]]
import ctypes  # noqa: E402
ctypes.windll.shcore.SetProcessDpiAwareness(1)
root = gui.TkinterDnD.Tk() if gui._DND else gui.tk.Tk()
app = gui.App(root)
media = [p for p in paths if p.suffix.lower() != ".pdf"]
pdfs = [p for p in paths if p.suffix.lower() == ".pdf"]
app._add_paths(media)
if pdfs and app.items:
    app.tree.selection_set(app.items[-1].iid)
    app._add_paths(pdfs)
app.adv_open.set(True)
app._toggle_adv()
root.geometry(f"{int(1020 * app.scale)}x{int(800 * app.scale)}+20+20")


root.attributes("-topmost", True)
root.lift()


def report():
    root.update_idletasks()
    print(root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height(), flush=True)


root.after(2500, report)
root.after(7000, root.destroy)
root.mainloop()
