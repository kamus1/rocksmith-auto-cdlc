# Plan: local web interface

**Status (2026-11-07):** steps 1–3 done (`python -m cdlc web`). What remains is the part of the verification (step 4) that needs the game and the browser: a build deployed from the page and tested in Rocksmith. Written on 2026-10-24.

## Goal
A website that **only runs on the user's PC**: started with `python -m cdlc web`, opened at `http://127.0.0.1:8000`, and listening only on localhost. It replaces the terminal for daily use and will be the face of the project when the repo is public.

It has to be local for three reasons:
- It needs the user's **Wwise, GPU and `dlc/` folder**.
- A public website where people upload copyrighted audio would be a legal problem, and the Wwise license is not meant for that.
- This way there are no server costs.

## Decisions made
- **FastAPI + an HTML page with framework-free JavaScript**, instead of Gradio or a desktop app. Reasons:
  - It reuses all the Python in `cdlc/`.
  - It allows showing live progress (SSE) and a custom interface.
  - It is lightweight, with no build step.
  - Later on it can serve as the interface for the "make me song X" agent.
- **Interface first, then prepare the public repo** (see "Afterwards").

## Steps

### 1. Separate the build logic from the CLI — done
`cdlc/pipeline.py` (`BuildOptions`, `build_song`, `BuildResult`, `PipelineError`, `inspect_tab`, `build_tones`). Seven Nation Army gives exactly the same bar times as v8. ffmpeg errors and missing files give a clear message.

- Move from `cdlc/cli.py` to a new module `cdlc/pipeline.py`: constants, `find_wwise`, `rsbuild`, `resolve_tone`, `choose_tracks`, `lookup_song`, `download_cover`, `deploy` and the body of `cmd_build`.
- Turn `cmd_build` into `build_song(opts, on_step, on_log) -> BuildResult`:
  - `opts` is a dataclass with the same fields as the current arguments.
  - `on_step(n, title)` and `on_log(line)` replace the `print` calls.
  - It returns the paths of the psarc, report, cover and deployed song, plus the metrics.
- Replace `SystemExit` with a custom `PipelineError` exception, so the web can show the error.
- The stray `print` calls in `lookup_song`, `download_cover` and `deploy` now call `on_log`.
- `cli.py` remains a thin argparse layer over `pipeline.build_song`, with the same output as now.
- Verification: `python -m cdlc build --audio … --tab …` gives the same result as before with Seven Nation Army.

### 2. Backend (`cdlc/web/app.py`) — done
Tested via the API with Seven Nation Army: progress over SSE, a single job at a time (409 if another is running) and a clear error with a tab that is not Guitar Pro. Uploads older than 2 days are deleted at startup. There is no GPU/CUDA in the checks: it is no longer needed. `/api/jobs/current` allows recovering progress if the page is reloaded.

- Dependencies: `fastapi`, `uvicorn`, `python-multipart` (add them to `requirements.txt`).
- Uploaded files in `build/_uploads/<id>/`, which git already ignores.
- Endpoints:
  - `GET /`: the page.
  - `POST /api/inspect` (tab): tracks (`RsBuild inspect-json`), the automatic choice from `choose_tracks` and the metadata taken from the tab.
  - `POST /api/build` (audio, tab id, lead, rhythm, tone, metadata, deploy yes/no): runs `build_song` in a thread and returns the job id. Only one build at a time (GPU and Wwise).
  - `GET /api/jobs/{id}/events`: SSE with steps 0–6, the log lines and the final result or error.
  - `GET /api/songs`: songs generated in `build/*/pkg` (cover, report, version deployed in `dlc/`).
  - `POST /api/songs/{name}/deploy`: copy it to `dlc/` again as a new `_vN`.
  - `GET /api/songs/{name}/cover` and `GET /api/songs/{name}/report`.
  - `GET /api/tones`: categories and state of the library. `POST /api/tones/rebuild` runs `RsBuild tones`.
  - `GET /api/status`: environment checks (compatible Wwise, ffmpeg, GPU/CUDA, `dlc/` folder, tone library), to show clear warnings.
- Listen only on `127.0.0.1`. Subcommand `python -m cdlc web [--port 8000] [--no-browser]` that opens the browser.

### 3. Frontend (`cdlc/web/static/index.html` + `app.js` + `style.css`) — done, not reviewed in the browser
- **New song**:
  1. Drag in the audio and the tab.
  2. Track table (name, strings, tuning, notes, chords, coverage, instrument) with Lead and Rhythm selectors pre-filled with the automatic choice.
  3. Editable metadata (artist, title, album, year) and a preview of the iTunes cover.
  4. Tone: automatic, category or file.
  5. "Copy to dlc/" checkbox and **Generate** button.
- **Progress**: the 6 steps with state and time, collapsible log, alignment error and, when finished, the notice "go back to the game's title screen".
- **My songs**: cards with cover, artist and title, deployed version, "view report" and "redeploy" buttons.
- **Environment status**: warnings if Wwise 2019–2023, ffmpeg, CUDA or the tone library is missing.
- Dark theme, no external dependencies (everything served locally).

### 4. Verification
- Seven Nation Army end to end from the web: it appears in the game after going back to the title screen.
- Errors: a tab that is not Guitar Pro, corrupt audio, missing Wwise. Each one shows a clear message on the page.
- The CLI keeps working the same.

## Afterwards: prepare the public repo
- Configuration in a file (`config.toml` or environment variables) instead of hard-coded paths: Rocksmith's `dlc/` folder, Wwise, ffmpeg.
- README in English or bilingual, with screenshots of the web, requirements, installation and a legal notice: use your own audio, do not share unreviewed charts and follow the CustomsForge rules.
- `LICENSE` (e.g. MIT) and review the dependencies' licenses: Rocksmith2014.NET MIT, alphaTab MPL-2.0. (beat_this is no longer used.)
- Confirm that nothing copyrighted gets uploaded: `samples/`, `build/tones` (tones from other CDLC), audio.
- Decide which name and email to sign the commits with before publishing them (the personal email currently appears).
- Create the repo on GitHub (the user confirms it).

## Pending outside this plan
- Test Master of Puppets `_v3` in the game (robust alignment) and report the stretches that drift.
- Ideas for later: automated tests, several tones per song, combining riff and chords in one arrangement, "make me song X" agent, transcription without a tab.
