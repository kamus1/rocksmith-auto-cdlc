# Rocksmith CDLC: research

Research from 2026-10-22 on what Rocksmith CDLC are, how they are made by hand and how the process could be automated. **Status (2026-10-24): MVP working on Windows** — audio + Guitar Pro tab → playable `.psarc`, tested in the game.

## Usage

Requirements: .NET 10 SDK, ffmpeg, Wwise 2019–2023 (Authoring package; 2024+ is not compatible) and Python 3.12.

```
git submodule update --init
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
dotnet build src/RsBuild -c Release

.venv\Scripts\python -m cdlc inspect --tab song.gp             # show tracks and tunings
.venv\Scripts\python -m cdlc tones                             # tone library from the installed CDLC
.venv\Scripts\python -m cdlc web                               # local web interface at http://127.0.0.1:8000
.venv\Scripts\python -m cdlc build --audio song.mp3 --tab song.gp --lead 0 [--rhythm 1] \
    --artist "..." --title "..." [--album ... --year ...] [--tone distortion]
```

`build` converts the audio (adds 4 s of silence for the count-in), aligns each bar of the tab with the audio (harmony with a plausible tempo model, then drums and attacks; see `docs/ALIGNMENT.md`), generates the XML (notes, chords with names and fingering, techniques, anchors, phrases), converts with Wwise, packages with DD and copies the result to `dlc/` as a new `_vN`. To make the game load it, just go back to the title screen; do not replace a `.psarc` with the same name while the game is open.

Parts: `src/RsBuild` (F#: alphaTab + Rocksmith2014.NET) and `cdlc/` (Python: audio, alignment, CLI). Each build leaves a `report.md` in `build/<song>/pkg/`.

Known limitations: alignment assumes the tab has the same structure as the recording (no extra or missing repeats) and can fail in long stretches where the tab has almost no notes; one tone per arrangement; no tuning changes within an arrangement.

---

## 1. What a CDLC is

**CDLC** stands for *Custom DLC*: fan-made songs for **Rocksmith 2014 Remastered** (PC/Mac). They work like official DLC. Each one is a `.psarc` file containing:

- The audio in Wwise format (`.wem`), plus a preview of about 30 s
- The charts (arrangements) for Lead, Rhythm, Bass and vocals
- Dynamic difficulty (DD)
- Amp and pedal tones
- Cover art and metadata

**Community:** CustomsForge. Its [Ignition](https://ignition4.customsforge.com) database has tens of thousands of songs. **Before making a song, it is worth searching for it there.**

**Things to keep in mind:**
- It only works on **Rocksmith 2014 on PC/Mac**. There is no support on consoles or in Rocksmith+.
- The game needs the **CDLC Enabler**, installed with the CustomsForge installer. The `.psarc` files go in `Rocksmith2014/dlc/`.
- Rocksmith 2014 was pulled from stores in October 2023 and returned to Steam in December 2024 as the "Learn & Play" edition.
- It is a legal grey area, because the audio is copyrighted. The community rule is to use audio you have bought.

## 2. Manual process

### Tools

| Tool | What it is for |
|---|---|
| **EOF (Editor on Fire)** | Chart editor, the core of the process. You need the full version plus the *hotfix*. |
| **DLC Builder** (iminashi, v3.5.0 from July 2025) | Packages into `.psarc`, generates DD, manages tones and validates. Runs on Windows, macOS and Linux. |
| **Wwise** (2019/2021/2022/2023) | Converts the audio to `.wem`. DLC Builder invokes it. |
| **Guitar Pro / TuxGuitar** | Starting tab, imported by EOF. |
| **Audacity** | Prepares the audio. |
| **UltraStar Creator** (optional) | Lyrics. |

### Workflow
1. Get good-quality audio. Optionally, add about 10 s of silence at the start.
2. Get the tab in Guitar Pro and **add an empty bar at the start**. Otherwise EOF errors out for lack of a *count-in*.
3. **In EOF:** import the audio and the tab, and **sync the beat map to the recording**. This is the most laborious part. Then review the techniques, sections, fret hand positions and chords, and export the XML.
4. **In DLC Builder:** create the project, add the XML, audio, cover art, tones and tuning, generate DD and press **Build** to get the `.psarc`.
5. Copy the file to `dlc/`, test it in the game and repeat steps 3 and 4 until it is right.

Community advice: learn to write and fix tabs before diving in.

### Linux
- **Rocksmith with Proton.** Low latency requires the chain PipeWire-JACK → wineASIO → RS_ASIO → Rocksmith. The reference guide is [theNizo/linux_rocksmith](https://github.com/theNizo/linux_rocksmith). Its mirror warns about problems with wine 10.2 or later; check the current state before starting.
- **CDLC Enabler on Linux:** reports are contradictory.
  - The [CachyOS/Arch guide (Dec. 2025)](https://www.devroom.io/2025/12/02/rocksmith-2014-on-cachyos-arch-linux/) says the recent enabler crashes the game and uses an old `D3DX9_42.dll` as a workaround.
  - A [CustomsForge thread (Jan. 2025)](https://customsforge.com/topic/67471-cdlc-on-linux-instructions/) says it works by running the installer with wine inside `compatdata/221680/pfx`.
- **Tools:** DLC Builder has a native Linux version. EOF and Wwise are Windows-only and would have to be used with wine (untested). Rocksmith2014.NET looks for Wwise in `~/.wine/drive_c/Program Files (x86)/Audiokinetic/`.

## 3. Automation

### State of the art
- **There is no automatic CDLC generator** from audio. None was found.
- **Packaging can be scripted.** [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET) is the engine behind DLC Builder: F# (with some C#), MIT license and active (last push on 2026-10-12). Relevant modules in `src/`:
  - `Rocksmith2014.XML` / `.XML.Processing`: read, write and validate arrangements. Also on [NuGet](https://www.nuget.org/packages/Rocksmith2014.XML).
  - `Rocksmith2014.DD`: dynamic difficulty generator.
  - `Rocksmith2014.Audio`: Wwise conversion (`Wwise/`, `WwiseFinder.fs`) and preview.
  - `Rocksmith2014.DLCProject`: `PackageBuilder.buildPackages` generates the `.psarc`. Also `ShowLightGenerator`, `ToolkitImporter` and `PsarcImport`.
  - `Rocksmith2014.EOF`: export to an EOF project.
  - `Rocksmith2014.PSARC` and `Rocksmith2014.SNG`: binary formats.
  - Python alternative for PSARC: [rs-utils](https://github.com/0x0L/rs-utils).
- **Audio-to-tab transcription:** still at the research stage. It works in simple cases and fails with polyphony and techniques.
  - [TART](https://arxiv.org/pdf/2609.11904): detects techniques (bends, slides, harmonics).
  - [Fretting-Transformer](https://papers.cool/arxiv/2506.14223): goes from MIDI to string and fret with T5.
  - [AutoTab](https://sol.sbc.org.br/index.php/semish/article/view/36816): CNN, 87.8% accuracy on single notes.
  - Known tools (not verified in this research): Demucs (stem separation, `htdemucs_6s` includes guitar), Spotify's Basic Pitch (audio to MIDI), beat_this and madmom (beat and downbeat detection).

### CustomsForge rules on AI
According to the [CDLC Agreement](https://ignition4.customsforge.com/kb/article/Rules%20and%20Guidelines/cdlc-agreement), AI may be used for audio tasks, such as creating stems. **Tabs generated solely by AI are prohibited**, and any CDLC made with AI help must follow the charting guidelines and be tested thoroughly. For personal use there is no restriction.

### Proposed system

**Input:** the song's audio and a Guitar Pro tab (`.gp`/`.gp5`/`.gpx`). Optionally, metadata (artist, title, album, year).
**Output:** a `.psarc` ready for `dlc/`, optionally an EOF project and a validation report.

```
Audio + Guitar Pro tab
   │
   ├─ 1. Separate stems (Demucs)                                  ✅ automatable
   ├─ 2. Detect beats and bars → beat map                         ✅ automatable (the most tedious part by hand)
   ├─ 3. Detect sections (verse, chorus…) → phrases               ✅ automatable
   ├─ 4. Notes:
   │      A) Align the tab with the audio (stretch each bar)      ✅ fairly reliable
   │      B) Transcribe from audio (Basic Pitch + fret assignment) ⚠️ draft only
   ├─ 5. Generate Rocksmith XML + validate                        ✅ Rocksmith2014.XML
   ├─ 6. DD, genre-based tones, cover art and metadata            ✅ Rocksmith2014.DD
   ├─ 7. Audio to .wem (Wwise via wine) + package .psarc          ✅ Rocksmith2014.Audio / DLCProject
   └─ 8. Play it and review it                                    👤 human
```

**Role of the agent (LLM):** orchestrate the pipeline. Look up metadata and cover art, infer the tuning, run the tools, read the validator's errors and fix them (count-in, impossible hand positions), and choose the tones. **It must not transcribe**; the audio models do that.

**Suggested MVP:** a Python CLI (audio analysis and alignment) that calls an F# script (`dotnet fsi`) with Rocksmith2014.NET for the XML, DD, audio and packaging. Option A comes first. Option B and the agent layer ("make me song X") are left for later.

**Limitations:**
- If the tab has errors, so will the CDLC.
- If the tab is of a different version of the song (live, remaster), alignment fails in some parts. The report should flag it.
- The final test in the game is manual.
- You must use your own audio. Scraping tabs from Songsterr is not advisable because its terms of use forbid it.

### Pending before building
- [ ] Is Rocksmith 2014 installed on this PC with Proton?
- [ ] Pick a test song (audio and Guitar Pro tab), ideally with a simple structure.
- [ ] Guitar, bass or both?
- [ ] Check that Wwise installs and works with wine.
- [ ] Review `samples/` and `PackageBuilder.fs` in Rocksmith2014.NET to see how to build a `DLCProject` from a script.

## 4. Sources

**Manual process (CustomsForge):**
- [Which software to use for creating CDLC](https://www.editoronfire.customsforge.com/kb/article/Charting/which-software-to-use)
- [arrov's CDLC creation process step by step](https://customsforge.com/topic/66003-arrovs-cdlc-creation-process-step-by-step/)
- [CDLC creation for Rocksmith 2014 Remastered](https://customsforge.com/topic/35318-cdlc-creation-for-rocksmith-2014-remastered/)
- [Advice regarding CDLC creation](https://customsforge.com/topic/67553-advice-and-question-regarding-cdlc-creation/)
- [Best way to unpack and edit a CDLC](https://customsforge.com/topic/80257-best-way-to-unpack-and-edit-a-cdlc/)
- [DLC Builder](https://cfmanager.customsforge.com/tools/DLCBuilder)
- [CDLC Agreement](https://ignition4.customsforge.com/kb/article/Rules%20and%20Guidelines/cdlc-agreement)

**Rocksmith on Linux:**
- [theNizo/linux_rocksmith](https://github.com/theNizo/linux_rocksmith)
- [Rocksmith 2014 on CachyOS/Arch Linux](https://www.devroom.io/2025/12/02/rocksmith-2014-on-cachyos-arch-linux/)
- [CDLC on Linux instructions (CustomsForge)](https://customsforge.com/topic/67471-cdlc-on-linux-instructions/)
- [ProtonDB – Rocksmith 2014](https://protondb.com/app/221680)

**Libraries and transcription:**
- [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET)
- [Rocksmith2014.XML (NuGet)](https://www.nuget.org/packages/Rocksmith2014.XML)
- [rs-utils](https://github.com/0x0L/rs-utils)
- [TART](https://arxiv.org/pdf/2609.11904)
- [Fretting-Transformer](https://papers.cool/arxiv/2506.14223)
- [AutoTab](https://sol.sbc.org.br/index.php/semish/article/view/36816)

**General:**
- [Rocksmith 2014 – Wikipedia](https://en.wikipedia.org/wiki/Rocksmith_2014)
