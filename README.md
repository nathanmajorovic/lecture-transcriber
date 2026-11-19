# Lecture Transcriber

Zet lesopnames (video **of** enkel audio) lokaal om naar een transcript met tijdstempels.
Bij video zoekt de app ook de slidewissels en koppelt ze, als je de slides-PDF erbij geeft,
aan de echte slidenummers. Alles draait offline op je eigen pc: geen API's, de audio gaat
nergens naartoe.

Het transcript is bedoeld om daarna zelf, samen met de slides, aan een LLM te geven voor een
slide-by-slide samenvatting. `prompt_template.md` bevat daarvoor een kant-en-klare instructie.


**Waarom?** Een opname rechtstreeks aan een chat-LLM geven om ze te laten transcriberen kost veel
credits. Deze tool doet het zware werk (spraak naar tekst) lokaal en gratis, op je eigen GPU of CPU.
De LLM krijgt daarna alleen nog tekst, en dat is veel goedkoper.

> Opnames, slides en transcripten staan **niet** in deze repo: ze worden uitgesloten via
> `.gitignore`. Alles blijft op je eigen pc.

## Snelstart

```
git clone https://github.com/nathanmajorovic/lecture-transcriber.git
cd lecture-transcriber
install.bat
```

Daarna start je de app via de snelkoppeling **Lecture Transcriber** op je bureaublad. Sleep een
opname en de slides-PDF erin, kies het vak en klik op **Start**. Hieronder staat alles in detail.

---

## 1. Installatie (eenmalig)

Nodig: Windows 10/11, ±5 GB vrije schijfruimte en internet (alleen voor de installatie).
Een NVIDIA-kaart is niet verplicht, maar wel ±5× sneller.

1. **Python 3.10 of nieuwer**, als je dat nog niet hebt:
   ```
   winget install Python.Python.3.12
   ```
2. **Dubbelklik op `install.bat`.** Dat doet de rest:
   - installeert **ffmpeg** via winget (`winget install Gyan.FFmpeg`) als het ontbreekt;
   - maakt een virtuele omgeving in `%USERPROFILE%\.venvs\lecture-transcriber`
     (bewust **buiten OneDrive**, want die bevat ±3 GB aan bibliotheken);
   - installeert de Python-pakketten, inclusief de **CUDA-bibliotheken** cuBLAS en cuDNN
     (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) voor de GPU. Een aparte CUDA-installatie of
     PATH-aanpassing is niet nodig;
   - downloadt de modellen (`large-v3-turbo` voor de GPU, `small` als reserve voor de CPU);
   - test of de GPU werkt (`tests\cuda_check.py`);
   - zet een snelkoppeling **Lecture Transcriber** op het bureaublad en in het Startmenu.

Daarna werkt alles offline.

## 2. Gebruik

### Met het venster
Start via de snelkoppeling of `start_app.bat`.

1. **Sleep** opnames naar de wachtrij, of klik op *Opnames toevoegen*.
   Bestanden die alleen in "DEEL 1" / "DEEL 2" verschillen, worden automatisch één les,
   met een scheiding `=== DEEL 2 ===` in het transcript.
2. Het **vak** wordt geraden uit de map (bv. `...\MACHINE LEARNING\...` → `ml`).
   Klopt het niet, selecteer dan de les en kies het vak in de keuzelijst.
3. **Slides-PDF** (optioneel): sleep de PDF erbij, of selecteer de les en klik op
   *Slides-PDF koppelen*.
4. Klik op **Start**. Bovenaan zie je hoeveel RAM en VRAM er vrij is. Per les zie je de
   voortgang en de resterende tijd. Na ±1 minuut audio verschijnt een preview met de taal en
   de eerste regels: zo zie je meteen of het de juiste opname en taal is.
5. **Pauzeer** of **Annuleer** kan altijd. Na annuleren, een crash of het uitzetten van de pc
   gaat de volgende *Start* verder vanaf het laatst afgewerkte stuk.
6. Dubbelklik op een afgewerkte les om de uitvoermap te openen.

Onder *Geavanceerde instellingen*: model, taal, beam_size, threads, stukgrootte,
slidegebied, alleen CPU, en **Enkel fragment** (bv. start `20:00`, duur `2:00`) om snel te
testen of alles goed staat.

### Met de commandoregel
```
lecture-transcribe.bat opname.mp4 --course ml --slides "Rule learning.pdf"
lecture-transcribe.bat "les 7 DEEL_1.m4a" "les 7 DEEL_2.m4a" --course wiskunde-ai
lecture-transcribe.bat opname.mp4 --course ml --clip 20:00 2:00      (test op 2 minuten)
lecture-transcribe.bat --list-courses
lecture-transcribe.bat --help
```
Je kan opnames ook gewoon **op `lecture-transcribe.bat` slepen** (dan zonder vak; de taal wordt
dan automatisch gedetecteerd).

| Optie | Betekenis |
|---|---|
| `--course`, `-c` | vakprofiel uit `courses.yaml` |
| `--slides` | slides-PDF van deze les |
| `--language`, `-l` | `nl`, `en`, … of `auto` (standaard: uit het vakprofiel) |
| `--model`, `-m` | `large-v3-turbo` (standaard op GPU), `large-v3`, `medium`, `small` |
| `--beam` | beam_size (standaard 5 op GPU, 1 op CPU) |
| `--cpu` | GPU niet gebruiken |
| `--chunk-min` | stukgrootte in minuten (standaard 20) |
| `--threads` | CPU-threads (standaard de helft van de kernen) |
| `--out` | vaste uitvoermap (standaard: naast de opname) |
| `--separate` | DEEL 1/2 niet samenvoegen |
| `--clip START DUUR` | alleen een stuk verwerken (om te testen) |
| `--crop x,y,b,h` | slidegebied in het beeld zelf instellen |
| `--no-slides` | geen slidedetectie |
| `--force` | opnieuw verwerken, ook als de les al klaar is |
| `--yes`, `-y` | niet vragen bij weinig geheugen |

## 3. Wat je krijgt

Een map `<lesnaam>_transcript\`, naast de opname of in je vaste uitvoermap:

| Bestand | Inhoud |
|---|---|
| `transcript.txt` | **hoofdproduct**: regels `[mm:ss] tekst` (`[h:mm:ss]` bij opnames boven 1 uur) |
| `transcript_per_slide.md` | (video) hetzelfde transcript met kopjes `=== Slide 7 (12:34 – 18:02, 5 min 28 s) ===`, en bovenaan de tijd per slide (⭐ = langst besproken) |
| `slides_timeline.csv` | (video) slidenummer, start, einde, duur, zekerheid; opent meteen in Excel |
| `slides\` | schermafbeeldingen: zonder PDF van elke slide, met PDF alleen van de onzekere |
| `info.txt` | model, taal, prompt, verwerkingstijd, waarschuwingen |
| `prompt_template.md` | instructie om samen met het transcript en de slides naar een LLM te sturen |
| `logs\` | een logbestand per run |

**Zekerheid van de slidenummers:**
- `Slide 7`: zeker.
- `Slide 7?`: waarschijnlijk (bijna-identieke pagina's, bv. dezelfde tabel met een ander rijtje gemarkeerd).
- `Slide ?`: onzeker (geen goede overeenkomst, bv. een demo in de browser of het bord). Bekijk dan de schermafbeelding.

Als het beeld nooit stabiel is (bv. een camera die de zaal filmt), meldt de app dat de
slidedetectie niet bruikbaar was en krijg je gewoon het transcript.

## 4. Zo haal je er het meeste uit

### Voor je start
1. **Maak per vak een profiel** (*+ Nieuw vak*) met de juiste taal en een jargonlijst: namen van
   personen en methodes (Arnoldi, Levenberg-Marquardt), afkortingen (GMRES, LS-SVM) en vaktermen.
   Lees na de eerste les het transcript snel door, en voeg woorden die fout geschreven werden toe
   aan het profiel.
2. **Koppel altijd de slides-PDF.** Dat helpt dubbel: termen uit de PDF gaan mee in de prompt van
   Whisper (betere spelling van jargon), en bij video krijg je de echte slidenummers.
3. **Zet de taal vast** in het vakprofiel. Detecteren is minder betrouwbaar dan vastzetten.
4. **Test een nieuw vak eerst kort**: *Geavanceerde instellingen → Enkel fragment* (bv. start
   `20:00`, duur `2:00`). Zo zie je binnen een minuut of taal, jargon en slidedetectie kloppen.

### Tijdens het verwerken
- **Lader erin.** Op batterij vertraagt de GPU sterk.
- **Hou minstens 3 GB RAM vrij.** Sluit browsers, IDE's en andere zware apps (dat is het grootste
  risico, niet de GPU).
- **Zet meerdere lessen in de wachtrij** en laat ze lopen. Geef elke les het juiste vak en de
  juiste PDF: selecteer de rij, kies het *Vak* en klik op *Slides-PDF koppelen*.
- **Kijk na ±1 minuut naar de preview** in het verloop: klopt de taal, en is het de juiste opname?
- **Klep dicht laten werken?** Kan, als Windows dan niets doet: *Settings → System → Power &
  battery → Lid & power button controls → "Closing the lid" = Do nothing* (Plugged in), en bij
  *Screen, sleep & hibernate timeouts* de slaapstand op *Never* (Plugged in). Zet de laptop op een
  harde, vlakke ondergrond.
- **Gestopt of gecrasht?** Gewoon opnieuw *Start*: afgewerkte lessen worden overgeslagen, en een
  half afgewerkte les gaat verder vanaf het laatste stuk.

### Na het verwerken: samenvatting met een LLM
1. Open de uitvoermap (dubbelklik op de les, of *Open uitvoermap*).
2. Upload naar je LLM:
   - `transcript_per_slide.md` (video), of `transcript.txt` (enkel audio);
   - de slides-PDF en/of de cursusnota's.
3. Plak de instructie uit `prompt_template.md` erbij en vraag om een slide-by-slide samenvatting.
4. Tips voor een betere samenvatting:
   - Slides met ⭐ zijn het langst besproken: meestal de belangrijkste stof.
   - Vraag de LLM om examentips expliciet te markeren ("exam", "important", "dit moet je kennen").
   - Vreemde woorden zijn bijna altijd verkeerd verstaan jargon: de instructie vraagt om ze met de
     slides te controleren.
   - Bij `Slide ?` (onzeker): bekijk de schermafbeelding in `slides\` (vaak bordwerk of een demo).
   - Is de les te lang voor één keer? Geef het transcript dan per deel (bv. per uur).

## 5. Een vak toevoegen

**In de app:** klik naast de keuzelijst *Vak* op **+ Nieuw vak** (of **Bewerk** voor het gekozen
vak). Vul de naam, de taal, een korte intro en de jargontermen in (één per regel) en klik op
*Opslaan*. De app schrijft het vak naar `courses.yaml`.

**Met de hand:** open `courses.yaml` in Kladblok en kopieer een bestaand blok:

```yaml
statistiek:
  title: Statistiek          # ook gebruikt om het vak te raden uit de mapnaam
  language: nl               # nl, en, … of auto
  model:                     # leeg = automatisch
  intro: "Les Statistiek, prof X."
  jargon:
    - betrouwbaarheidsinterval
    - nulhypothese
    - p-waarde
```

- **Let op de inspringing**: twee spaties, en een streepje voor elke term.
- Zet in `jargon` de termen die Whisper verkeerd spelt: namen (Arnoldi, Lanczos), afkortingen
  (GMRES, KKT) en vakjargon. Ze gaan mee in de *initial prompt* (max. ±224 tokens). Eerst komt
  het vakprofiel, daarna de beste termen uit de slides-PDF.
- **Taal vastzetten** werkt beter dan detecteren. Gebruik `auto` alleen als het vak in beide
  talen gegeven wordt. De detectie gebeurt dan op 25%, 50% en 75% van de opname, niet op het
  (vaak stille) begin.

## 6. Snelheid en systeem

Gemeten op een RTX 3050 Ti Laptop (4 GB), op batterij:

| Opname | Tijd |
|---|---|
| ML-les, video 46 min, met slides-PDF | 6 min 54 s (3 min 47 s transcriptie + slides) |
| Audio 52 min | ±5 min |

Ruwweg 1 minuut per 10 minuten audio, plus ±3 min per uur video voor de slides.
Met de lader in gaat het sneller. Alleen op de CPU (`small`): ±3× realtime, en merkbaar minder
nauwkeurig.

De app probeert je pc bruikbaar te houden:
- lage prioriteit (BELOW_NORMAL), ook voor ffmpeg;
- standaard de helft van de CPU-kernen;
- de audio wordt per stuk ingelezen, de video nooit volledig;
- één les tegelijk, en nooit twee modellen tegelijk geladen;
- bij minder dan 3 GB vrij RAM vraagt de app eerst om apps te sluiten, en daarna gebruikt
  ze kleinere stukken;
- bij een tekort aan GPU-geheugen valt de app automatisch terug: eerst `large-v3-turbo`,
  dan beam 1, dan CPU;
- tijdelijke bestanden staan in `%USERPROFILE%\.lecture-transcriber\work\` (buiten OneDrive)
  en worden achteraf opgeruimd.

## 7. Veelvoorkomende problemen

| Probleem | Oplossing |
|---|---|
| "ffmpeg niet gevonden" | `winget install Gyan.FFmpeg`, daarna de app opnieuw starten |
| "CUDA werkt niet, ik gebruik de CPU" | NVIDIA-driver bijwerken (GeForce Experience / nvidia.com). Daarna `install.bat` opnieuw draaien; de laatste stap test de GPU |
| "Weinig geheugen" | Apps in het systeemvak afsluiten (rechtermuisknop → *Afsluiten*; het kruisje sluit ze vaak niet echt), of de pc herstarten. Uitzetten via *Taakbeheer → Opstart-apps* werkt pas na een herstart |
| Verkeerde taal of onzin in de preview | Annuleer, en kies de juiste taal (vakprofiel of *Geavanceerd → Taal*). Nederlands forceren op een Engelse les geeft onzin, en omgekeerd |
| Vreemde woorden in het transcript ("armoldi", "kriel of") | Normaal: dat is verkeerd verstaan jargon. Zet de term in `jargon` van het vak; de LLM-instructie vraagt ook om het met de slides te controleren |
| Slides allemaal "onzeker" | Controleer of de PDF bij deze les hoort. Staat de slide maar in een deel van het beeld en wordt dat niet gevonden: stel het slidegebied zelf in (`--crop x,y,b,h` in pixels van de video) |
| "is al verwerkt" | De les is klaar. Gebruik `--force` (of verwijder de uitvoermap) om opnieuw te beginnen |
| Proces gestopt (crash, pc uit) | Start gewoon opnieuw: het gaat verder vanaf het laatst afgewerkte stuk |
| Slepen werkt niet | Gebruik *Opnames toevoegen …*, of draai `install.bat` opnieuw (pakket `tkinterdnd2`) |

## 8. Structuur

```
lecture-transcriber/
├─ install.bat, start_app.bat, lecture-transcribe.bat, make_shortcut.ps1
├─ courses.yaml                 vakprofielen
├─ requirements.txt, pyproject.toml
├─ src/lecture_transcriber/
│  ├─ system.py      ffmpeg vinden, prioriteit, RAM/VRAM/schijf/batterij
│  ├─ audio.py       video of audio?, omzetten naar 16 kHz mono WAV, per stuk inlezen
│  ├─ device.py      GPU/CPU-keuze, CUDA-bibliotheken
│  ├─ config.py      vakprofielen en instellingen
│  ├─ prompt.py      initial_prompt (vakprofiel + termen uit de PDF)
│  ├─ transcribe.py  faster-whisper, fallback bij geheugentekort, taaldetectie, pauze
│  ├─ job.py         één les verwerken, hervatten, uitvoer
│  ├─ slides/        frames.py, detect.py, match.py
│  ├─ output.py      alle uitvoerbestanden
│  ├─ cli.py, gui.py
└─ tests/            cuda_check.py, slides_debug.py, gui_smoke.py, gui_run.py
```
