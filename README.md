# Lecture Transcriber

Turn lecture recordings (video **or** audio only) into a timestamped transcript, locally on your
own PC. For video recordings, the app also detects slide changes and, if you add the slides PDF,
links them to the actual slide numbers. Everything runs offline: no APIs, and your audio never
leaves your computer.

The transcript is meant to be handed to an LLM **afterwards**, together with the slides, to get a
slide-by-slide summary. `prompt_template.md` contains a ready-made instruction for that.

**Why?** Uploading a recording straight to a chat LLM and asking it to transcribe costs a lot of
credits. This tool does the heavy lifting (speech to text) locally and for free, on your own GPU or
CPU. The LLM then only gets text, which is far cheaper.

> Recordings, slides and transcripts are **not** part of this repo: they are excluded through
> `.gitignore`. Everything stays on your own PC.

> **Note on the interface language:** the app's interface is in Dutch. Where this README mentions
> a button or setting, the Dutch label is shown in *italics*, with a translation where useful.

## Quick start

```
git clone https://github.com/nathanmajorovic/lecture-transcriber.git
cd lecture-transcriber
install.bat
```

Then start the app via the **Lecture Transcriber** shortcut on your desktop. Drag in a recording
and its slides PDF, pick the course and click **Start**. Everything is explained in detail below.

---

## 1. Installation (one-time)

Requirements: Windows 10/11, ±5 GB free disk space, and an internet connection (for installation
only). An NVIDIA GPU is not required, but makes it roughly 5× faster.

1. **Python 3.10 or newer**, if you don't have it yet:
   ```
   winget install Python.Python.3.12
   ```
2. **Double-click `install.bat`.** It takes care of the rest:
   - installs **ffmpeg** via winget (`winget install Gyan.FFmpeg`) if it is missing;
   - creates a virtual environment in `%USERPROFILE%\.venvs\lecture-transcriber`
     (deliberately **outside OneDrive**, since it holds ±3 GB of libraries);
   - installs the Python packages, including the **CUDA libraries** cuBLAS and cuDNN
     (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) for the GPU. No separate CUDA install or PATH
     changes are needed;
   - downloads the models (`large-v3-turbo` for the GPU, `small` as a CPU fallback);
   - checks that the GPU works (`tests\cuda_check.py`);
   - puts a **Lecture Transcriber** shortcut on your desktop and in the Start menu.

After that, everything works offline.

## 2. Usage

### With the app window
Start it via the shortcut or `start_app.bat`.

1. **Drag** recordings into the queue (*Wachtrij*), or click *Opnames toevoegen* (add recordings).
   You can add several at once; they are processed one after the other.
   Files that only differ in "DEEL 1" / "DEEL 2" (part 1 / part 2) automatically become one
   lecture, with a `=== DEEL 2 ===` separator in the transcript. To keep them separate, tick
   *DEEL 1/2 niet samenvoegen*.
2. The **course** (*Vak*) is guessed from the folder name (e.g. `...\MACHINE LEARNING\...` → `ml`).
   If it's wrong, select the lecture and pick the course from the dropdown.
3. **Slides PDF** (optional): drag the PDF in, or select the lecture and click
   *Slides-PDF koppelen* (link slides PDF).
4. Click **Start**. The top bar shows how much RAM and VRAM is free. Each lecture shows its
   progress and remaining time. After ±1 minute of audio, a preview with the detected language and
   the first lines appears, so you can check right away that it's the right recording and language.
5. You can always pause (*Pauzeer*) or cancel (*Annuleer*). After cancelling, a crash or the PC
   shutting down, the next *Start* resumes from the last completed chunk.
6. Double-click a finished lecture to open its output folder.

Under *Geavanceerde instellingen* (advanced settings): model, language, beam size, threads, chunk
size, slide region, CPU only, and **Enkel fragment** (clip only, e.g. start `20:00`, duration
`2:00`) to quickly test whether everything is set up correctly.

### From the command line
```
lecture-transcribe.bat recording.mp4 --course ml --slides "Rule learning.pdf"
lecture-transcribe.bat "lecture 7 DEEL_1.m4a" "lecture 7 DEEL_2.m4a" --course wiskunde-ai
lecture-transcribe.bat recording.mp4 --course ml --clip 20:00 2:00      (test on 2 minutes)
lecture-transcribe.bat --list-courses
lecture-transcribe.bat --help
```
You can also simply **drag recordings onto `lecture-transcribe.bat`** (no course profile in that
case; the language is detected automatically).

| Option | Meaning |
|---|---|
| `--course`, `-c` | course profile from `courses.yaml` |
| `--slides` | slides PDF for this lecture |
| `--language`, `-l` | `nl`, `en`, … or `auto` (default: from the course profile) |
| `--model`, `-m` | `large-v3-turbo` (default on GPU), `large-v3`, `medium`, `small` |
| `--beam` | beam size (default 5 on GPU, 1 on CPU) |
| `--cpu` | don't use the GPU |
| `--chunk-min` | chunk length in minutes (default 20) |
| `--threads` | CPU threads (default: half of the cores) |
| `--out` | fixed output folder (default: next to the recording) |
| `--separate` | don't merge DEEL 1/2 |
| `--clip START DURATION` | process only a fragment (for testing) |
| `--crop x,y,w,h` | set the slide region in the video frame manually |
| `--no-slides` | skip slide detection |
| `--force` | reprocess, even if the lecture is already done |
| `--yes`, `-y` | don't ask when memory is low |

## 3. What you get

A folder `<lecture name>_transcript\`, either next to the recording (*Naast de opname*, the
default: in the same folder as the recording file) or in a fixed output folder (*In vaste map*):

| File | Contents |
|---|---|
| `transcript.txt` | **main output**: lines `[mm:ss] text` (`[h:mm:ss]` for recordings over 1 hour) |
| `transcript_per_slide.md` | (video) the same transcript with headings like `=== Slide 7 (12:34 – 18:02, 5 min 28 s) ===`, and at the top the time spent per slide (⭐ = discussed longest) |
| `slides_timeline.csv` | (video) slide number, start, end, duration, confidence; opens directly in Excel |
| `slides\` | screenshots: of every slide without a PDF, only of the uncertain ones with a PDF |
| `info.txt` | model, language, prompt, processing time, warnings |
| `prompt_template.md` | instruction to send to an LLM together with the transcript and slides |
| `logs\` | one log file per run |

**Confidence of the slide numbers:**
- `Slide 7`: certain.
- `Slide 7?`: probable (near-identical pages, e.g. the same table with a different row highlighted).
- `Slide ?`: uncertain (no good match, e.g. a browser demo or the blackboard). Check the screenshot.

If the image is never stable (e.g. a camera filming the lecture hall), the app reports that slide
detection wasn't usable and you simply get the transcript.

## 4. Getting the most out of it

### Before you start
1. **Create a profile per course** (*+ Nieuw vak*, new course) with the right language and a
   jargon list: names of people and methods (Arnoldi, Levenberg-Marquardt), acronyms (GMRES,
   LS-SVM) and technical terms. After the first lecture, skim the transcript and add any words
   that were misspelled to the profile.
2. **Always link the slides PDF.** It helps twice: terms from the PDF are added to Whisper's
   prompt (better spelling of jargon), and for video you get the real slide numbers.
3. **Fix the language** in the course profile. Detection is less reliable than a fixed language.
4. **Test a new course with a short clip first**: *Geavanceerde instellingen → Enkel fragment*
   (e.g. start `20:00`, duration `2:00`). Within a minute you'll see whether language, jargon and
   slide detection are right.

### While processing
- **Keep the charger plugged in.** On battery, the GPU slows down considerably.
- **Keep at least 3 GB of RAM free.** Close browsers, IDEs and other heavy apps (running out of
  RAM is the biggest risk, not the GPU).
- **Queue several lectures** and let them run. Give each lecture the right course and PDF: select
  the row, choose the *Vak* and click *Slides-PDF koppelen*.
- **Check the preview after ±1 minute** in the log (*Verloop*): is the language right, and is it
  the right recording?
- **Running with the lid closed?** That works if Windows does nothing when you close it:
  *Settings → System → Power & battery → Lid & power button controls → "Closing the lid will make
  my PC" = Do nothing* (Plugged in), and under *Screen, sleep & hibernate timeouts* set sleep to
  *Never* (Plugged in). Put the laptop on a hard, flat surface.
- **Stopped or crashed?** Just press *Start* again: finished lectures are skipped, and a
  half-finished lecture resumes from its last completed chunk.

### After processing: summarizing with an LLM
1. Open the output folder (double-click the lecture, or *Open uitvoermap*).
2. Upload to your LLM:
   - `transcript_per_slide.md` (video), or `transcript.txt` (audio only);
   - the slides PDF and/or the course notes.
3. Paste the instruction from `prompt_template.md` and ask for a slide-by-slide summary.
4. Tips for a better summary:
   - Slides marked ⭐ were discussed longest: usually the most important material.
   - Ask the LLM to explicitly flag exam hints ("exam", "important", "you need to know this").
   - Odd words are almost always misheard jargon: the instruction asks the LLM to check them
     against the slides.
   - For `Slide ?` (uncertain): look at the screenshot in `slides\` (often blackboard work or a
     demo).
   - Lecture too long for one go? Give the transcript in parts (e.g. per hour).

## 5. Adding a course

**In the app:** next to the *Vak* dropdown, click **+ Nieuw vak** (new course), or **Bewerk**
(edit) for the selected course. Fill in the name (*Naam van het vak*), the language (*Taal*), a
short intro (*Korte intro*) and the jargon terms (*Jargon*, one per line), then click *Opslaan*
(save). The app writes the course to `courses.yaml`.

**By hand:** open `courses.yaml` in Notepad and copy an existing block:

```yaml
statistics:
  title: Statistics          # also used to guess the course from the folder name
  language: en               # nl, en, … or auto
  model:                     # empty = automatic
  intro: "Statistics lecture, prof X."
  jargon:
    - confidence interval
    - null hypothesis
    - p-value
```

- **Mind the indentation**: two spaces, and a dash before each term.
- Put the terms Whisper gets wrong in `jargon`: names (Arnoldi, Lanczos), acronyms (GMRES, KKT)
  and technical vocabulary. They go into the *initial prompt* (max. ±224 tokens). The course
  profile comes first, then the best terms from the slides PDF.
- **Fixing the language** works better than detection. Only use `auto` if the course is taught
  in both languages. Detection then runs at 25%, 50% and 75% of the recording, not at the
  (often silent) start.

## 6. Speed and system

Measured on an RTX 3050 Ti Laptop GPU (4 GB), on battery:

| Recording | Time |
|---|---|
| ML lecture, 46 min video, with slides PDF | 6 min 54 s (3 min 47 s transcription + slides) |
| 52 min audio | ±5 min |

Roughly 1 minute per 10 minutes of audio, plus ±3 min per hour of video for the slides.
It's faster with the charger plugged in. CPU only (`small`): ±3× real time, and noticeably less
accurate.

The app tries to keep your PC usable:
- low priority (BELOW_NORMAL), for ffmpeg too;
- by default half of the CPU cores;
- audio is read chunk by chunk, video is never loaded in full;
- one lecture at a time, and never two models loaded at once;
- with less than 3 GB of free RAM, the app first asks you to close apps, and then uses smaller
  chunks;
- on a GPU out-of-memory error, it falls back automatically: first `large-v3-turbo`, then
  beam 1, then CPU;
- temporary files live in `%USERPROFILE%\.lecture-transcriber\work\` (outside OneDrive) and are
  cleaned up afterwards.

## 7. Troubleshooting

| Problem | Solution |
|---|---|
| "ffmpeg niet gevonden" (ffmpeg not found) | `winget install Gyan.FFmpeg`, then restart the app |
| "CUDA werkt niet, ik gebruik de CPU" (CUDA doesn't work, using CPU) | Update your NVIDIA driver (GeForce Experience / nvidia.com), then rerun `install.bat`; its last step tests the GPU |
| "Weinig geheugen" (low memory) | Quit apps from the system tray (right-click → *Quit*; the X often doesn't really close them), or restart the PC. Disabling apps in *Task Manager → Startup apps* only takes effect after a restart |
| Wrong language or gibberish in the preview | Cancel and pick the right language (course profile or *Geavanceerd → Taal*). Forcing Dutch on an English lecture produces gibberish, and vice versa |
| Odd words in the transcript ("armoldi", "kriel of") | Normal: that's misheard jargon. Add the term to the course's `jargon`; the LLM instruction also asks it to check against the slides |
| All slides "uncertain" | Check that the PDF belongs to this lecture. If the slide only fills part of the frame and isn't found, set the slide region yourself (`--crop x,y,w,h` in video pixels) |
| "is al verwerkt" (already processed) | The lecture is done. Use `--force` (or delete the output folder) to start over |
| Process stopped (crash, PC off) | Just start again: it resumes from the last completed chunk |
| Drag and drop doesn't work | Use *Opnames toevoegen …*, or rerun `install.bat` (package `tkinterdnd2`) |

## 8. Project structure

```
lecture-transcriber/
├─ install.bat, start_app.bat, lecture-transcribe.bat, make_shortcut.ps1
├─ courses.yaml                 course profiles
├─ requirements.txt, pyproject.toml
├─ src/lecture_transcriber/
│  ├─ system.py      find ffmpeg, priority, RAM/VRAM/disk/battery
│  ├─ audio.py       video or audio?, convert to 16 kHz mono WAV, read chunk by chunk
│  ├─ device.py      GPU/CPU choice, CUDA libraries
│  ├─ config.py      course profiles and settings
│  ├─ prompt.py      initial prompt (course profile + terms from the PDF)
│  ├─ transcribe.py  faster-whisper, out-of-memory fallback, language detection, pause
│  ├─ job.py         process one lecture, resume, output
│  ├─ slides/        frames.py, detect.py, match.py
│  ├─ output.py      all output files
│  ├─ cli.py, gui.py
└─ tests/            cuda_check.py, slides_debug.py, gui_smoke.py, gui_run.py
```
