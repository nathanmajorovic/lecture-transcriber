"""
Eenvoudig venster (Tkinter, geen zware installatie).

- Bestanden slepen of kiezen; DEEL_1/DEEL_2 worden automatisch één les.
- Per les: vak (automatisch geraden uit de map, aanpasbaar) en optioneel een slides-PDF.
- Start / Pauzeer / Annuleer. Per les een voortgangsbalk en de geschatte resterende tijd.
- Eén les tegelijk, in een aparte thread; het venster blijft bruikbaar.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from .config import Course, RunOptions, load_courses, save_course, slugify   # noqa: E402
from .job import Lesson, group_files, run_lesson            # noqa: E402
from .output import fmt_dur, fmt_ts                         # noqa: E402
from .system import Cancelled, FriendlyError, default_threads, resources   # noqa: E402
from .transcribe import Control                             # noqa: E402

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND = True
except Exception:  # pragma: no cover
    _DND = False

MEDIA_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v",
             ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma"}
SETTINGS = Path.home() / ".lecture-transcriber" / "gui.json"
MODELS = ["automatisch", "large-v3-turbo", "large-v3", "medium", "small"]
LANGS = ["uit vakprofiel", "nl", "en", "auto"]


@dataclass
class Item:
    lesson: Lesson
    course: str
    pdf: Path | None = None
    status: str = "wachtend"
    progress: float = 0.0          # 0..1
    eta: float | None = None
    out_dir: Path | None = None
    iid: str = ""
    warnings: list[str] = field(default_factory=list)


def guess_course(path: Path, courses: dict[str, Course]) -> str | None:
    p = str(path).lower().replace("_", " ")
    for key, c in courses.items():
        if c.title.lower() in p or f"\\{key.lower()}" in p or f"/{key.lower()}" in p:
            return key
    return None


def bar(f: float, width: int = 10) -> str:
    n = int(round(max(0.0, min(1.0, f)) * width))
    return "━" * n + "┄" * (width - n) + f"  {100 * f:3.0f}%"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.courses = load_courses()
        self.items: list[Item] = []
        self.events: queue.Queue = queue.Queue()
        self.control: Control | None = None
        self.worker: threading.Thread | None = None
        self.current: Item | None = None
        self.settings = self._load_settings()

        root.title("Lecture Transcriber")
        s = self.scale = max(1.0, root.winfo_fpixels("1i") / 96)   # schermschaal (bv. 1.5 bij 150%)
        root.geometry(self.settings.get("geometry", f"{int(1020 * s)}x{int(780 * s)}"))
        root.minsize(int(860 * s), int(640 * s))
        self._build()
        self._refresh_resources()
        root.after(100, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ opbouw

    # macOS-achtige kleuren
    BG, CARD, LINE, TEXT, MUTED = "#f5f5f7", "#ffffff", "#e3e3e8", "#1d1d1f", "#86868b"
    ACCENT, ACCENT_DARK, SELECT = "#0a84ff", "#0071e3", "#e3effd"

    def _theme(self):
        fams = set(tk.font.families())
        base = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Helvetica") if f in fams), "TkDefaultFont")
        self.font = (base, 10)
        self.font_b = (base, 10, "bold")
        self.font_h = (base, 15, "bold")
        self.font_s = (base, 9)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tk.font.nametofont(name).configure(family=base, size=10)
            except tk.TclError:
                pass
        r = self.root
        r.configure(background=self.BG)
        st = ttk.Style()
        st.theme_use("clam")
        s = self.scale
        st.configure(".", background=self.BG, foreground=self.TEXT, font=self.font,
                     bordercolor=self.LINE, lightcolor=self.CARD, darkcolor=self.LINE,
                     troughcolor=self.LINE, focuscolor=self.ACCENT, selectbackground=self.SELECT,
                     selectforeground=self.TEXT, insertcolor=self.TEXT)
        st.configure("Card.TFrame", background=self.CARD, relief="flat")
        st.configure("Card.TLabel", background=self.CARD)
        st.configure("Card.TCheckbutton", background=self.CARD)
        st.configure("Card.TRadiobutton", background=self.CARD)
        st.map("Card.TCheckbutton", background=[("active", self.CARD)])
        st.map("Card.TRadiobutton", background=[("active", self.CARD)])
        st.configure("Title.TLabel", font=self.font_h)
        st.configure("Section.TLabel", font=self.font_b, foreground=self.TEXT)
        st.configure("Muted.TLabel", foreground=self.MUTED, font=self.font_s)
        st.configure("CardMuted.TLabel", background=self.CARD, foreground=self.MUTED, font=self.font_s)
        # knoppen: wit met dunne rand; primaire knop blauw
        st.configure("TButton", background=self.CARD, bordercolor=self.LINE, lightcolor=self.CARD,
                     darkcolor=self.CARD, relief="flat", padding=(int(12 * s), int(5 * s)))
        st.map("TButton", background=[("disabled", self.BG), ("pressed", "#e8e8ed"), ("active", "#f0f0f3")],
               foreground=[("disabled", "#b0b0b5")], bordercolor=[("focus", self.ACCENT)])
        st.configure("Accent.TButton", background=self.ACCENT, foreground="white", bordercolor=self.ACCENT,
                     lightcolor=self.ACCENT, darkcolor=self.ACCENT, font=self.font_b,
                     padding=(int(18 * s), int(6 * s)))
        st.map("Accent.TButton", background=[("disabled", "#a8cffb"), ("pressed", self.ACCENT_DARK),
                                             ("active", self.ACCENT_DARK)],
               foreground=[("disabled", "white")], bordercolor=[("active", self.ACCENT_DARK)])
        st.configure("Link.TButton", background=self.BG, foreground=self.ACCENT, bordercolor=self.BG,
                     lightcolor=self.BG, darkcolor=self.BG, padding=(0, 2))
        st.map("Link.TButton", background=[("active", self.BG)], foreground=[("active", self.ACCENT_DARK)])
        # invoervelden
        for w in ("TEntry", "TCombobox"):
            st.configure(w, fieldbackground=self.CARD, bordercolor=self.LINE, lightcolor=self.CARD,
                         darkcolor=self.CARD, padding=int(4 * s), arrowcolor=self.MUTED)
            st.map(w, bordercolor=[("focus", self.ACCENT)], lightcolor=[("focus", self.ACCENT)],
                   fieldbackground=[("readonly", self.CARD)])
        r.option_add("*TCombobox*Listbox.background", self.CARD)
        r.option_add("*TCombobox*Listbox.selectBackground", self.SELECT)
        r.option_add("*TCombobox*Listbox.selectForeground", self.TEXT)
        r.option_add("*TCombobox*Listbox.font", self.font)
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, indicatorcolor=self.CARD, indicatorbackground=self.CARD,
                         indicatorsize=int(14 * s), indicatormargin=(0, 0, int(6 * s), 0),
                         upperbordercolor=self.MUTED, lowerbordercolor=self.MUTED)
        st.map("TCheckbutton", indicatorcolor=[("selected", self.ACCENT)], background=[("active", self.BG)])
        st.map("TRadiobutton", indicatorcolor=[("selected", self.ACCENT)], background=[("active", self.BG)])
        # lijst
        st.configure("Treeview", background=self.CARD, fieldbackground=self.CARD, bordercolor=self.CARD,
                     lightcolor=self.CARD, darkcolor=self.CARD, rowheight=int(28 * s), relief="flat")
        st.map("Treeview", background=[("selected", self.SELECT)], foreground=[("selected", self.TEXT)])
        st.configure("Treeview.Heading", background=self.CARD, foreground=self.MUTED, relief="flat",
                     bordercolor=self.CARD, lightcolor=self.CARD, darkcolor=self.CARD, font=self.font_s,
                     padding=(int(6 * s), int(4 * s)))
        st.map("Treeview.Heading", background=[("active", self.CARD)])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])   # geen kader
        # voortgangsbalk: dun en blauw
        st.configure("Horizontal.TProgressbar", background=self.ACCENT, troughcolor=self.LINE,
                     bordercolor=self.LINE, lightcolor=self.ACCENT, darkcolor=self.ACCENT,
                     thickness=int(6 * s))
        st.configure("Vertical.TScrollbar", background=self.CARD, troughcolor=self.CARD,
                     bordercolor=self.CARD, lightcolor=self.CARD, darkcolor=self.CARD,
                     arrowcolor=self.MUTED, gripcount=0)
        st.map("Vertical.TScrollbar", background=[("active", self.LINE)])

    def _card(self, title: str, subtitle: str = "", expand: bool = False) -> ttk.Frame:
        """Sectietitel + witte kaart (in plaats van een ouderwets kader)."""
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        ttk.Label(hdr, text=title, style="Section.TLabel").pack(side="left")
        if subtitle:
            ttk.Label(hdr, text="  " + subtitle, style="Muted.TLabel").pack(side="left")
        card = ttk.Frame(self.root, style="Card.TFrame", padding=int(8 * self.scale))
        card.pack(fill="both" if expand else "x", expand=expand, padx=16)
        return card

    def _build(self):
        import tkinter.font  # noqa: F401  (voor tk.font)
        self._theme()
        pad = {"padx": 16, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=16, pady=(14, 0))
        ttk.Label(top, text="Lecture Transcriber", style="Title.TLabel").pack(side="left")
        self.res_var = tk.StringVar(value="Geheugen controleren …")
        ttk.Label(top, textvariable=self.res_var, style="Muted.TLabel").pack(side="right")

        # --- wachtrij
        qf = self._card("Wachtrij", "sleep opnames en slides-PDF's hierheen", expand=True)
        cols = ("vak", "pdf", "status", "voortgang", "rest")
        self.tree = ttk.Treeview(qf, columns=cols, show="tree headings", height=7, selectmode="extended")
        self.tree.heading("#0", text="Les / opnames")
        self.tree.column("#0", width=int(300 * self.scale))
        for c, w, t in (("vak", 95, "Vak"), ("pdf", 130, "Slides-PDF"), ("status", 140, "Status"),
                        ("voortgang", 150, "Voortgang"), ("rest", 75, "Nog")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=int(w * self.scale), anchor="w")
        sb = ttk.Scrollbar(qf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_course_box())
        self.tree.bind("<Double-1>", lambda e: self._open_selected())
        if _DND:
            for w in (self.tree, qf):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Button(btns, text="Opnames toevoegen …", command=self._choose_files).pack(side="left")
        ttk.Button(btns, text="Slides-PDF koppelen …", command=self._choose_pdf).pack(side="left", padx=6)
        ttk.Button(btns, text="PDF loskoppelen", command=self._clear_pdf).pack(side="left")
        ttk.Button(btns, text="Verwijderen", command=self._remove).pack(side="left", padx=6)
        ttk.Label(btns, text="Vak:").pack(side="left", padx=(16, 4))
        self.course_var = tk.StringVar(value=self.settings.get("course", next(iter(self.courses), "")))
        self.course_box = ttk.Combobox(btns, textvariable=self.course_var, state="readonly", width=16,
                                       values=list(self.courses))
        self.course_box.pack(side="left")
        self.course_box.bind("<<ComboboxSelected>>", lambda e: self._apply_course())
        ttk.Button(btns, text="Bewerk", command=lambda: self._course_dialog(self.course_var.get())
                   ).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="+ Nieuw vak", command=lambda: self._course_dialog(None)
                   ).pack(side="left", padx=6)
        self.separate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="DEEL 1/2 niet samenvoegen", variable=self.separate_var
                        ).pack(side="right")

        # --- uitvoer + geavanceerd
        of = self._card("Uitvoer")
        self.out_mode = tk.StringVar(value=self.settings.get("out_mode", "naast"))
        ttk.Radiobutton(of, text="Naast de opname", value="naast", variable=self.out_mode,
                        style="Card.TRadiobutton").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Radiobutton(of, text="In vaste map:", value="vast", variable=self.out_mode,
                        style="Card.TRadiobutton").grid(row=0, column=1, sticky="w", padx=6)
        self.out_dir = tk.StringVar(value=self.settings.get("out_dir", ""))
        ttk.Entry(of, textvariable=self.out_dir, width=50).grid(row=0, column=2, sticky="we", padx=4)
        ttk.Button(of, text="…", width=3, command=self._choose_out).grid(row=0, column=3, padx=4)
        of.columnconfigure(2, weight=1)

        self.adv_open = tk.BooleanVar(value=False)
        self.adv_btn = ttk.Button(self.root, text="▸ Geavanceerde instellingen", style="Link.TButton",
                                  command=lambda: (self.adv_open.set(not self.adv_open.get()),
                                                   self._toggle_adv()))
        self.adv_btn.pack(anchor="w", padx=16, pady=(8, 0))
        self.adv = ttk.Frame(self.root, style="Card.TFrame", padding=int(8 * self.scale))
        a = self.adv
        self.model_var = tk.StringVar(value=self.settings.get("model", "automatisch"))
        self.lang_var = tk.StringVar(value="uit vakprofiel")
        self.beam_var = tk.StringVar(value="")
        self.threads_var = tk.StringVar(value=str(default_threads()))
        self.chunk_var = tk.StringVar(value="20")
        self.crop_var = tk.StringVar(value="")
        self.slides_var = tk.BooleanVar(value=True)
        self.cpu_var = tk.BooleanVar(value=False)
        self.clip_start = tk.StringVar(value="")
        self.clip_dur = tk.StringVar(value="")
        row = 0
        for label, widget in (
            ("Model", ttk.Combobox(a, textvariable=self.model_var, values=MODELS, state="readonly", width=16)),
            ("Taal", ttk.Combobox(a, textvariable=self.lang_var, values=LANGS, state="readonly", width=16)),
            ("beam_size (leeg = auto)", ttk.Entry(a, textvariable=self.beam_var, width=6)),
            ("CPU-threads", ttk.Entry(a, textvariable=self.threads_var, width=6)),
            ("Stukken (min)", ttk.Entry(a, textvariable=self.chunk_var, width=6)),
        ):
            ttk.Label(a, text=label, style="Card.TLabel").grid(row=row // 3, column=(row % 3) * 2,
                                                               sticky="e", padx=(8, 4), pady=3)
            widget.grid(row=row // 3, column=(row % 3) * 2 + 1, sticky="w")
            row += 1
        ttk.Label(a, text="Slidegebied x,y,b,h", style="Card.TLabel").grid(row=2, column=0, sticky="e",
                                                                          padx=(8, 4))
        ttk.Entry(a, textvariable=self.crop_var, width=18).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Checkbutton(a, text="Slidedetectie", variable=self.slides_var, style="Card.TCheckbutton"
                        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=8)
        ttk.Checkbutton(a, text="Enkel CPU", variable=self.cpu_var, style="Card.TCheckbutton"
                        ).grid(row=2, column=4, sticky="w")
        ttk.Label(a, text="Enkel fragment (test): start", style="Card.TLabel").grid(row=3, column=0,
                                                                                   sticky="e", padx=(8, 4))
        ttk.Entry(a, textvariable=self.clip_start, width=8).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Label(a, text="duur", style="Card.TLabel").grid(row=3, column=2, sticky="e", padx=(8, 4))
        ttk.Entry(a, textvariable=self.clip_dur, width=8).grid(row=3, column=3, sticky="w")
        ttk.Label(a, text="mm:ss, leeg = volledige opname", style="CardMuted.TLabel"
                  ).grid(row=3, column=4, columnspan=2, sticky="w", padx=8)

        # --- bediening + huidige voortgang
        ctl = ttk.Frame(self.root)
        ctl.pack(fill="x", padx=16, pady=(14, 4))
        self.start_btn = ttk.Button(ctl, text="Start", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(ctl, text="Pauzeer", command=self._pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(ctl, text="Annuleer", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.open_btn = ttk.Button(ctl, text="Open uitvoermap", command=self._open_selected,
                                   state="disabled")
        self.open_btn.pack(side="right")

        pf = ttk.Frame(self.root)
        pf.pack(fill="x", **pad)
        self.phase_var = tk.StringVar(value="Klaar om te starten.")
        ttk.Label(pf, textvariable=self.phase_var, style="Muted.TLabel").pack(anchor="w")
        self.pbar = ttk.Progressbar(pf, maximum=1000)
        self.pbar.pack(fill="x", pady=(4, 0))

        lf = self._card("Verloop", expand=True)
        self.root.pack_slaves()[-1].pack_configure(pady=(0, 16))
        self.log = tk.Text(lf, height=8, wrap="word", font=self.font_s, relief="flat", borderwidth=0,
                           highlightthickness=0, background=self.CARD, foreground=self.TEXT,
                           padx=4, pady=2, spacing1=1, spacing3=1, state="disabled")
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="left", fill="y")
        self.log.tag_configure("warn", foreground="#b25000")
        self.log.tag_configure("err", foreground="#d70015")
        self.log.tag_configure("ok", foreground="#248a3d")
        self.log.tag_configure("preview", background="#f2f7ff", lmargin1=12, lmargin2=12,
                               foreground="#1d3d6b")
        self.log.tag_configure("bold", font=(self.font_s[0], self.font_s[1], "bold"))
        if not _DND:
            self._log("Slepen werkt niet (tkinterdnd2 ontbreekt): gebruik 'Opnames toevoegen'.", "warn")

    # ------------------------------------------------------------------ hulp

    def _log(self, text: str, tag: str | None = None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _toggle_adv(self):
        if self.adv_open.get():
            self.adv.pack(fill="x", padx=16, after=self.adv_btn)
            self.adv_btn.configure(text="▾ Geavanceerde instellingen")
        else:
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸ Geavanceerde instellingen")

    def _refresh_resources(self):
        def work():
            try:
                r = resources()
                txt = r.describe()
                if r.ram_available_gb < 3:
                    txt += "   ⚠ weinig RAM: sluit andere apps"
                if r.on_battery:
                    txt = txt.replace("| op batterij", "|  ⚠ op batterij, trager")
                self.events.put(("res", txt))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()
        self.root.after(5000, self._refresh_resources)

    def _item_by_iid(self, iid: str) -> Item | None:
        while self.tree.parent(iid):
            iid = self.tree.parent(iid)
        return next((it for it in self.items if it.iid == iid), None)

    def _selected_items(self) -> list[Item]:
        out = []
        for iid in self.tree.selection():
            it = self._item_by_iid(iid)
            if it and it not in out:
                out.append(it)
        return out

    def _render(self, it: Item):
        vals = (it.course, it.pdf.name if it.pdf else "—", it.status,
                bar(it.progress) if it.progress > 0 else "",
                fmt_dur(it.eta) if it.eta else "")
        name = f"{it.lesson.name}  ({len(it.lesson.parts)} opnames)" if len(it.lesson.parts) > 1 \
            else it.lesson.parts[0].name
        if not it.iid:
            it.iid = self.tree.insert("", "end", text=name, values=vals, open=True)
            if len(it.lesson.parts) > 1:
                for k, p in enumerate(it.lesson.parts, 1):
                    self.tree.insert(it.iid, "end", text=f"   DEEL {k}: {p.name}", values=("", "", "", "", ""))
        else:
            self.tree.item(it.iid, text=name, values=vals)

    # ------------------------------------------------------------------ bestanden

    def _add_paths(self, paths: list[Path]):
        media = [p for p in paths if p.suffix.lower() in MEDIA_EXT and p.is_file()]
        pdfs = [p for p in paths if p.suffix.lower() == ".pdf" and p.is_file()]
        other = [p for p in paths if p not in media and p not in pdfs]
        for p in other:
            self._log(f"Overgeslagen (geen audio/video/PDF): {p.name}", "warn")
        new_items = []
        if media:
            known = {str(pp).lower() for it in self.items for pp in it.lesson.parts}
            media = [p for p in media if str(p).lower() not in known]
            for lesson in group_files(media, self.separate_var.get()):
                course = guess_course(lesson.parts[0], self.courses) or self.course_var.get()
                it = Item(lesson, course)
                self.items.append(it)
                self._render(it)
                new_items.append(it)
                self._log(f"Toegevoegd: {lesson.name} ({len(lesson.parts)} opname(s), vak: {course})")
        for pdf in pdfs:
            target = (self._selected_items() or new_items or
                      [it for it in self.items if it.pdf is None and it.status == "wachtend"][-1:])
            if not target:
                self._log(f"PDF '{pdf.name}': voeg eerst een opname toe of selecteer een les.", "warn")
                continue
            for it in target[:1]:
                it.pdf = pdf
                self._render(it)
                self._log(f"Slides '{pdf.name}' gekoppeld aan {it.lesson.name}")

    def _on_drop(self, event):
        paths = [Path(p) for p in self.root.tk.splitlist(event.data)]
        expanded = []
        for p in paths:
            if p.is_dir():
                expanded += sorted(x for x in p.iterdir() if x.suffix.lower() in MEDIA_EXT | {".pdf"})
            else:
                expanded.append(p)
        self._add_paths(expanded)

    def _choose_files(self):
        files = filedialog.askopenfilenames(
            title="Kies opnames (en eventueel slides-PDF's)",
            initialdir=self.settings.get("last_dir") or str(Path.home()),
            filetypes=[("Opnames en PDF", " ".join(f"*{e}" for e in sorted(MEDIA_EXT)) + " *.pdf"),
                       ("Alle bestanden", "*.*")])
        if files:
            self.settings["last_dir"] = str(Path(files[0]).parent)
            self._add_paths([Path(f) for f in files])

    def _choose_pdf(self):
        sel = self._selected_items()
        if not sel:
            messagebox.showinfo("Slides koppelen", "Selecteer eerst een les in de wachtrij.")
            return
        f = filedialog.askopenfilename(title="Kies de slides-PDF", filetypes=[("PDF", "*.pdf")],
                                       initialdir=self.settings.get("last_pdf_dir") or str(Path.home()))
        if f:
            self.settings["last_pdf_dir"] = str(Path(f).parent)
            for it in sel:
                it.pdf = Path(f)
                self._render(it)

    def _clear_pdf(self):
        for it in self._selected_items():
            it.pdf = None
            self._render(it)

    def _remove(self):
        for it in self._selected_items():
            if it is self.current:
                continue
            self.tree.delete(it.iid)
            self.items.remove(it)

    def _sync_course_box(self):
        sel = self._selected_items()
        if sel:
            self.course_var.set(sel[0].course)
            self.open_btn.configure(state="normal" if sel[0].out_dir else "disabled")

    def _apply_course(self):
        for it in self._selected_items():
            if it.status == "wachtend" or it.status.startswith("geannuleerd"):
                it.course = self.course_var.get()
                self._render(it)

    def _course_dialog(self, key: str | None):
        """Venster om een vak toe te voegen (key=None) of te bewerken. Schrijft naar courses.yaml."""
        c = self.courses.get(key) if key else None
        s = self.scale
        win = tk.Toplevel(self.root)
        win.title("Vak bewerken" if c else "Nieuw vak")
        win.configure(background=self.BG)
        win.transient(self.root)
        win.resizable(True, True)
        f = ttk.Frame(win, style="Card.TFrame", padding=int(14 * s))
        f.pack(fill="both", expand=True, padx=16, pady=16)
        title = tk.StringVar(value=c.title if c else "")
        lang = tk.StringVar(value=c.language if c else "nl")
        intro = tk.StringVar(value=c.intro if c else "")
        rows = (("Naam van het vak", ttk.Entry(f, textvariable=title, width=44)),
                ("Taal", ttk.Combobox(f, textvariable=lang, values=["nl", "en", "auto"], state="readonly",
                                      width=10)),
                ("Korte intro", ttk.Entry(f, textvariable=intro, width=44)))
        for r, (lbl, w) in enumerate(rows):
            ttk.Label(f, text=lbl, style="Card.TLabel").grid(row=r, column=0, sticky="e", padx=(0, 8), pady=4)
            w.grid(row=r, column=1, sticky="we" if r != 1 else "w", pady=4)
        ttk.Label(f, text="bv. \"Les Statistiek, prof X.\" (helpt Whisper met taal en stijl)",
                  style="CardMuted.TLabel").grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="Jargon", style="Card.TLabel").grid(row=4, column=0, sticky="ne", padx=(0, 8), pady=(10, 0))
        txt = tk.Text(f, width=44, height=12, font=self.font, relief="flat", wrap="word",
                      background=self.BG, highlightthickness=1, highlightbackground=self.LINE,
                      highlightcolor=self.ACCENT, padx=6, pady=4)
        txt.grid(row=4, column=1, sticky="nsew", pady=(10, 0))
        if c:
            txt.insert("1.0", "\n".join(c.jargon))
        ttk.Label(f, text="Eén term per regel: namen, afkortingen en vakwoorden die Whisper verkeerd schrijft.\n"
                          "De eerste termen zijn het belangrijkst (max. ±224 tokens samen met de PDF-termen).",
                  style="CardMuted.TLabel", justify="left").grid(row=5, column=1, sticky="w", pady=(4, 0))
        f.columnconfigure(1, weight=1)
        f.rowconfigure(4, weight=1)

        def save():
            t = title.get().strip()
            if not t:
                messagebox.showwarning("Nieuw vak", "Geef het vak een naam.", parent=win)
                return
            new_key = key or slugify(t)
            if not key and new_key in self.courses:
                messagebox.showwarning("Nieuw vak", f"Er bestaat al een vak '{new_key}'. Kies een andere naam "
                                                    "of gebruik Bewerk.", parent=win)
                return
            jargon = [j.strip(" ,;-•\t") for line in txt.get("1.0", "end").splitlines()
                      for j in line.split(";") if j.strip(" ,;-•\t")]
            course = Course(key=new_key, title=t, language=lang.get(), model=c.model if c else None,
                            intro=intro.get().strip(), jargon=jargon)
            try:
                save_course(course)
                self.courses = load_courses()
            except Exception as e:
                messagebox.showerror("Vak opslaan", f"Opslaan in courses.yaml lukte niet:\n{e}", parent=win)
                return
            self.course_box.configure(values=list(self.courses))
            self.course_var.set(new_key)
            self._apply_course()
            for it in self.items:          # titel kan gewijzigd zijn
                self._render(it)
            self._log(f"Vak {'bijgewerkt' if key else 'toegevoegd'}: {t} ({new_key}, {len(jargon)} termen)", "ok")
            win.destroy()

        bt = ttk.Frame(win)
        bt.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bt, text="Opslaan", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(bt, text="Annuleer", command=win.destroy).pack(side="right", padx=8)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + int(80 * s)}+{self.root.winfo_rooty() + int(60 * s)}")
        win.grab_set()
        (rows[0][1]).focus_set()

    def _choose_out(self):
        d = filedialog.askdirectory(title="Vaste uitvoermap")
        if d:
            self.out_dir.set(d)
            self.out_mode.set("vast")

    def _open_selected(self):
        sel = self._selected_items() or [it for it in self.items if it.out_dir]
        for it in sel[:1]:
            if it.out_dir and it.out_dir.exists():
                os.startfile(str(it.out_dir))

    # ------------------------------------------------------------------ uitvoeren

    def _options(self, it: Item) -> RunOptions:
        from .cli import parse_time
        course = self.courses[it.course]
        lang = self.lang_var.get()
        model = self.model_var.get()
        crop = None
        if self.crop_var.get().strip():
            try:
                x, y, w, h = (int(v) for v in self.crop_var.get().replace(" ", "").split(","))
                crop = (x, y, w, h)
            except ValueError:
                raise FriendlyError("Slidegebied: gebruik x,y,breedte,hoogte (bv. 80,128,480,352).")
        out_root = None
        if self.out_mode.get() == "vast":
            if not self.out_dir.get().strip():
                raise FriendlyError("Kies een vaste uitvoermap of zet de uitvoer op 'Naast de opname'.")
            out_root = Path(self.out_dir.get().strip())
        try:
            beam = int(self.beam_var.get()) if self.beam_var.get().strip() else None
            threads = max(1, int(self.threads_var.get()))
            chunk = max(2.0, float(self.chunk_var.get()))
            cs = parse_time(self.clip_start.get()) if self.clip_start.get().strip() else None
            cd = parse_time(self.clip_dur.get()) if self.clip_dur.get().strip() else None
        except ValueError:
            raise FriendlyError("Controleer de geavanceerde instellingen (getallen, tijden als mm:ss).")
        if cd is not None and cs is None:
            cs = 0.0
        return RunOptions(course=course, slides_pdf=it.pdf,
                          language=None if lang == "uit vakprofiel" else lang,
                          model=None if model == "automatisch" else model,
                          beam_size=beam, force_cpu=self.cpu_var.get(), chunk_min=chunk,
                          threads=threads, out_root=out_root, clip_start=cs, clip_duration=cd,
                          crop=crop, detect_slides=self.slides_var.get())

    def _start(self):
        todo = [it for it in self.items if it.status == "wachtend" or it.status.startswith("geannuleerd")
                or it.status.startswith("fout")]
        if not todo:
            messagebox.showinfo("Niets te doen", "Voeg eerst opnames toe aan de wachtrij.")
            return
        try:
            jobs = [(it, self._options(it)) for it in todo]
        except FriendlyError as e:
            messagebox.showwarning("Instellingen", str(e))
            return
        for it, opts in jobs:
            if opts.model == "large-v3":
                if not messagebox.askyesno("large-v3", "large-v3 op 4 GB VRAM is krap en ±2× trager dan "
                                                       "large-v3-turbo. Bij geheugentekort valt de app "
                                                       "terug op turbo.\n\nToch large-v3 gebruiken?"):
                    return
                break
        self._save_settings()
        self.control = Control()
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pauzeer")
        self.cancel_btn.configure(state="normal")
        for it, _ in jobs:
            it.status, it.progress, it.eta = "wachtend", 0.0, None
            self._render(it)
        self.worker = threading.Thread(target=self._run_jobs, args=(jobs, self.control), daemon=True)
        self.worker.start()

    def _pause(self):
        if not self.control:
            return
        if self.control.paused:
            self.control.resume()
            self.pause_btn.configure(text="Pauzeer")
            self._log("Hervat.")
            if self.current:
                self.current.status = self._resume_status
                self._render(self.current)
        else:
            self.control.pause()
            self.pause_btn.configure(text="Hervat")
            self._log("Gepauzeerd (het huidige stukje wordt eerst afgewerkt).", "warn")
            if self.current:
                self._resume_status = self.current.status
                self.current.status = "gepauzeerd"
                self._render(self.current)

    def _cancel(self):
        if self.control and messagebox.askyesno(
                "Annuleren", "Stoppen? Afgewerkte stukken blijven bewaard: bij de volgende start gaat "
                             "het verder waar het stopte."):
            self.control.cancel()

    # --- werkthread ---------------------------------------------------------

    def _ask_from_worker(self, title: str, text: str) -> bool:
        ev = threading.Event()
        box: dict = {}
        self.events.put(("ask", title, text, ev, box))
        ev.wait()
        return box.get("answer", False)

    def _run_jobs(self, jobs, control: Control):
        for it, opts in jobs:
            if control.cancelled:
                break
            self.events.put(("begin", it))

            def emit(kind, _it=it, **d):
                self.events.put(("emit", _it, kind, d))

            try:
                out = run_lesson(it.lesson, opts, emit, control, self._ask_from_worker)
                self.events.put(("finish", it, out))
            except Cancelled:
                self.events.put(("cancelled", it))
                break
            except FriendlyError as e:
                self.events.put(("error", it, str(e)))
            except Exception as e:   # onverwacht: toon kort, details staan in het logbestand
                self.events.put(("error", it, f"Onverwachte fout: {e!r}\nDetails staan in de map "
                                              f"'logs' van de uitvoermap."))
        self.events.put(("all_done",))

    # --- UI-lus -------------------------------------------------------------

    def _poll(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "res":
            self.res_var.set(ev[1])
        elif kind == "ask":
            _, title, text, flag, box = ev
            box["answer"] = messagebox.askyesno(title, text, icon="warning")
            flag.set()
        elif kind == "begin":
            it = ev[1]
            self.current = it
            it.status = "voorbereiden"
            self._render(it)
            self._log(f"\n▶ {it.lesson.name}", "bold")
        elif kind == "emit":
            self._on_emit(ev[1], ev[2], ev[3])
        elif kind == "finish":
            it, out = ev[1], ev[2]
            it.status, it.progress, it.eta, it.out_dir = "klaar ✓", 1.0, None, out
            self._render(it)
            self.open_btn.configure(state="normal")
        elif kind == "cancelled":
            it = ev[1]
            it.status = "geannuleerd (hervatbaar)"
            it.eta = None
            self._render(it)
            self._log("Geannuleerd. Klik later op Start om verder te gaan waar het stopte.", "warn")
        elif kind == "error":
            it, msg = ev[1], ev[2]
            it.status, it.eta = "fout", None
            self._render(it)
            self._log(f"✖ {msg}", "err")
            messagebox.showerror("Fout", f"{it.lesson.name}:\n\n{msg}")
        elif kind == "all_done":
            self.current = None
            self.control = None
            self.start_btn.configure(state="normal")
            self.pause_btn.configure(state="disabled", text="Pauzeer")
            self.cancel_btn.configure(state="disabled")
            self.phase_var.set("Klaar. Dubbelklik op een les om de uitvoermap te openen.")
            self.pbar["value"] = 0

    def _on_emit(self, it: Item, kind: str, d: dict):
        video_share = 0.8 if any(p.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
                                 for p in it.lesson.parts) and self.slides_var.get() else 1.0
        if kind == "progress":
            f = d["done"] / max(1e-6, d["total"])
            it.progress = f * video_share
            it.eta = d.get("eta")
            if not (self.control and self.control.paused):
                it.status = "transcriberen"
            speed = f", {d['speed']:.1f}× realtime" if d.get("speed") else ""
            self.phase_var.set(f"Transcriberen: {fmt_ts(d['done'], d['total'] >= 3600)} / "
                               f"{fmt_ts(d['total'], d['total'] >= 3600)}{speed}"
                               + (f" — nog ±{fmt_dur(d['eta'])}" if d.get("eta") else ""))
        elif kind == "slides_progress":
            stage_w = {"frames": (0.0, 0.75), "detect": (0.75, 0.85), "grab": (0.85, 1.0)}
            a, b = stage_w.get(d["stage"], (0, 1))
            it.progress = video_share + (1 - video_share) * (a + (b - a) * d["fraction"])
            it.status = "slides"
            it.eta = None
            names = {"frames": "beelden ophalen", "detect": "wissels zoeken", "grab": "slides vergelijken"}
            self.phase_var.set(f"Slides: {names.get(d['stage'], d['stage'])} {100 * d['fraction']:.0f}%")
        elif kind == "status":
            self._log(d["text"])
            if not (self.control and self.control.paused) and it.status in ("voorbereiden", "wachtend"):
                self.phase_var.set(d["text"])
        elif kind == "warning":
            self._log("⚠ " + d["text"], "warn")
        elif kind == "log":
            self._log("   " + d["text"])
        elif kind == "device":
            self._log(f"Toestel: {d['choice'].describe()}")
        elif kind == "preview":
            self._log(f"Eerste minuut — taal: {d['language']}. Klopt de opname en de taal?", "bold")
            for line in d["lines"]:
                self._log(line, "preview")
        elif kind == "done":
            self._log(f"✔ Klaar in {fmt_dur(d['elapsed'])}: {d['lines']} regels → {d['out_dir']}", "ok")
        self._render(it)
        self.pbar["value"] = int(1000 * it.progress)

    # ------------------------------------------------------------------ instellingen

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        self.settings.update({"course": self.course_var.get(), "out_mode": self.out_mode.get(),
                              "out_dir": self.out_dir.get(), "model": self.model_var.get(),
                              "geometry": self.root.geometry()})
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Afsluiten", "Er loopt nog een transcriptie. Stoppen en afsluiten?\n"
                                                    "(Afgewerkte stukken blijven bewaard.)"):
                return
            if self.control:
                self.control.cancel()
        self._save_settings()
        self.root.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:   # scherpe tekst op schermen met schaal > 100%
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    try:
        App(root)
    except FriendlyError as e:
        messagebox.showerror("Lecture Transcriber", str(e))
        return
    root.mainloop()


if __name__ == "__main__":
    main()
