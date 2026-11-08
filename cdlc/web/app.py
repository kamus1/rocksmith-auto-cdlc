"""Local web UI for the generator: python -m cdlc web (listens on 127.0.0.1 only).

Endpoints (see docs/WEB_PLAN.md): tab inspection, background build with SSE progress,
generated songs, tones and environment status. One job at a time (Wwise and memory).
"""

import asyncio
import json
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import pipeline
from ..pipeline import ROOT, TONE_CATEGORIES, TONES_DIR, PipelineError, dlc_dir

STATIC = Path(__file__).resolve().parent / "static"
UPLOADS = ROOT / "build" / "_uploads"
BUILD = ROOT / "build"
NOT_SONGS = {"_uploads", "bench", "cmp", "ref", "ref7", "ref_old", "tones"}

app = FastAPI(title="CDLC Generator", docs_url=None, redoc_url=None)


def _clean_uploads(max_age_s: float = 2 * 86400) -> None:
    """Uploads (copies of the audio and tab) are only needed during the build: those older than 2 days are deleted."""
    for d in UPLOADS.glob("*") if UPLOADS.is_dir() else []:
        if d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
            shutil.rmtree(d, ignore_errors=True)


_clean_uploads()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------- background jobs

class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.events: list[dict] = []
        self.finished = False
        self.started = time.time()

    def emit(self, **event) -> None:
        event["t"] = round(time.time() - self.started, 1)
        self.events.append(event)


JOBS: dict[str, Job] = {}
_busy = threading.Lock()


def start_job(kind: str, work) -> Job:
    """Runs work(job) in a thread. One at a time: if another is running, 409."""
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "A job is already running; wait for it to finish.")
    job = Job(kind)
    JOBS[job.id] = job

    def run():
        try:
            result = work(job)
            job.emit(type="done", result=result)
        except PipelineError as e:
            job.emit(type="error", message=str(e), detail=e.detail)
        except Exception as e:  # noqa: BLE001 — every failure must reach the page
            job.emit(type="error", message=f"Unexpected error: {e!r}", detail=traceback.format_exc())
        finally:
            job.finished = True
            _busy.release()

    threading.Thread(target=run, daemon=True).start()
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    # EventSource reconnects on its own: resume from the last event received
    start = int(request.headers.get("last-event-id", -1)) + 1

    async def stream():
        i = start
        while True:
            while i < len(job.events):
                yield f"id: {i}\ndata: {json.dumps(job.events[i], ensure_ascii=False)}\n\n"
                i += 1
            if job.finished and i >= len(job.events):
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/jobs/current")
def current_job():
    """The running job (to recover progress if the page is reloaded)."""
    running = [j for j in JOBS.values() if not j.finished]
    return {"id": running[-1].id, "kind": running[-1].kind} if running else {"id": None}


# ---------------------------------------------------------------- new song

def _save_upload(f: UploadFile, folder: Path) -> Path:
    name = re.sub(r"[^\w.\- ()&]+", "_", Path(f.filename or "file").name).strip() or "file"
    dest = folder / name
    with dest.open("wb") as out:
        shutil.copyfileobj(f.file, out)
    return dest


def _upload_dir(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", upload_id or ""):
        raise HTTPException(400, "Invalid upload id")
    d = UPLOADS / upload_id
    if not d.is_dir():
        raise HTTPException(404, "The upload no longer exists; choose the files again.")
    return d


@app.post("/api/inspect")
def inspect(tab: UploadFile = File(...)):
    """Uploads the tab and returns its tracks, the automatic Lead/Rhythm choice and the metadata."""
    upload_id = uuid.uuid4().hex[:12]
    folder = UPLOADS / upload_id
    folder.mkdir(parents=True)
    path = _save_upload(tab, folder)
    try:
        info = pipeline.inspect_tab(path)
        lead, rhythm, desc = pipeline.choose_tracks(info)
    except PipelineError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, f"Could not read the tab (is it a Guitar Pro file?): {e}")
    (folder / "tab.json").write_text(json.dumps({"file": path.name}), encoding="utf-8")
    return {"upload_id": upload_id, "filename": path.name, "info": info,
            "auto": {"lead": lead, "rhythm": rhythm, "description": desc}}


@app.post("/api/build")
def build(upload_id: str = Form(...), audio: UploadFile = File(...), lead: str = Form(""),
          rhythm: str = Form("auto"), tone: str = Form(""), artist: str = Form(""), title: str = Form(""),
          album: str = Form(""), year: str = Form(""), deploy: bool = Form(True), lookup: bool = Form(True),
          variant: str = Form(""), cover: UploadFile | None = File(None)):
    """Starts the build. rhythm: "auto", "none" or the track index. Empty lead: automatic."""
    folder = _upload_dir(upload_id)
    tab = folder / json.loads((folder / "tab.json").read_text(encoding="utf-8"))["file"]
    audio_path = _save_upload(audio, folder)
    cover_path = _save_upload(cover, folder) if cover is not None and cover.filename else None
    if tone and tone not in TONE_CATEGORIES:
        raise HTTPException(400, f"Unknown tone: {tone}")
    try:
        opts = pipeline.BuildOptions(
            audio=audio_path, tab=tab,
            lead=int(lead) if lead.strip() else None,
            rhythm=int(rhythm) if rhythm not in ("auto", "none", "") else None,
            no_rhythm=rhythm == "none",
            artist=artist.strip() or None, title=title.strip() or None, album=album.strip() or None,
            year=int(year) if year.strip() else None, cover=str(cover_path) if cover_path else None,
            tone=tone or None, deploy=deploy, lookup=lookup, variant=variant.strip() or None)
    except ValueError:
        raise HTTPException(400, "Invalid track or year")

    def work(job: Job) -> dict:
        r = pipeline.build_song(opts, on_step=lambda n, t: job.emit(type="step", n=n, of=pipeline.STEPS, title=t),
                                on_log=lambda line: job.emit(type="log", line=line))
        return {"song": r.out_dir.name, "psarc": str(r.psarc), "deployed": str(r.deployed) if r.deployed else None,
                "meta": {k: v for k, v in r.meta.items() if k != "cover"}, "tracks": r.tracks, "tone": r.tone,
                "gaps": r.alignment.get("gaps", []), "suspicious_bars": len(r.alignment.get("suspicious_bars", [])),
                "warnings": r.warnings, "version": _version(r.psarc.name)}

    job = start_job("build", work)
    return {"job_id": job.id}


# ---------------------------------------------------------------- generated songs

def _song_dir(name: str) -> Path:
    if not re.fullmatch(r"[\w.\-]+", name) or name in NOT_SONGS:
        raise HTTPException(400, "Invalid name")
    d = BUILD / name
    if not (d / "pkg").is_dir():
        raise HTTPException(404, "Unknown song")
    return d


def _psarc(d: Path) -> Path | None:
    files = sorted((d / "pkg").glob("*_p.psarc"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _version(name: str) -> int | None:
    m = re.search(r"_v(\d+)_p\.psarc$", name)
    return int(m.group(1)) if m else None


def _deployed(psarc: Path) -> str | None:
    key = re.sub(r"_v\d+_p\.psarc$", "", psarc.name)
    d = dlc_dir()
    found = sorted(d.glob(f"{key}_v*_p.psarc"), key=lambda f: _version(f.name) or 0) if d.is_dir() else []
    return found[-1].name if found else None


def _report_warnings(report: Path) -> list[str]:
    """Lines of the report's "## Warnings" section."""
    if not report.exists():
        return []
    out, inside = [], False
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Warnings"
        elif inside and line.startswith("- "):
            out.append(line[2:])
    return out


@app.get("/api/songs")
def songs():
    out = []
    for d in BUILD.iterdir() if BUILD.is_dir() else []:
        if d.name in NOT_SONGS or not d.is_dir() or (psarc := _psarc(d)) is None:
            continue
        report = d / "pkg" / "report.md"
        heading = report.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() if report.exists() else d.name
        artist, _, title = heading.partition(" - ")
        out.append({"name": d.name, "artist": artist, "title": title or heading, "psarc": psarc.name,
                    "built": psarc.stat().st_mtime, "deployed": _deployed(psarc), "version": _version(psarc.name),
                    "cover": (d / "cover.jpg").exists(), "report": report.exists(),
                    "warnings": _report_warnings(report)})
    return sorted(out, key=lambda s: -s["built"])


@app.post("/api/songs/{name}/deploy")
def redeploy(name: str):
    psarc = _psarc(_song_dir(name))
    if psarc is None:
        raise HTTPException(404, "No .psarc found")
    logs = []
    try:
        target = pipeline.deploy(psarc, logs.append)
    except PipelineError as e:
        raise HTTPException(400, str(e))
    return {"deployed": target.name, "log": logs}


@app.get("/api/songs/{name}/cover")
def song_cover(name: str):
    path = _song_dir(name) / "cover.jpg"
    if not path.exists():
        raise HTTPException(404, "No cover")
    return FileResponse(path)


@app.get("/api/songs/{name}/report", response_class=PlainTextResponse)
def song_report(name: str):
    path = _song_dir(name) / "pkg" / "report.md"
    if not path.exists():
        raise HTTPException(404, "No report")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- tones and environment

def _tone_index() -> list[dict]:
    path = TONES_DIR / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@app.get("/api/tones")
def tones():
    entries = _tone_index()
    counts = {c: sum(e["Descriptors"] in wanted for e in entries) for c, wanted in TONE_CATEGORIES.items()}
    return {"total": len(entries), "categories": counts}


@app.post("/api/tones/rebuild")
def tones_rebuild():
    def work(job: Job) -> dict:
        job.emit(type="step", n=0, of=0, title="Extracting tones from installed CDLC")
        for line in pipeline.build_tones().splitlines():
            job.emit(type="log", line=line)
        return {"total": len(_tone_index())}

    return {"job_id": start_job("tones", work).id}


@app.get("/api/status")
def status():
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg", path=str(pipeline.WINGET_LINKS))
    checks = [
        ("wwise", pipeline.find_wwise(), "Wwise 2019–2023 (Authoring package). 2024 or later is not supported."),
        ("ffmpeg", ffmpeg, "ffmpeg on the PATH (winget install Gyan.FFmpeg)."),
        ("dotnet", shutil.which("dotnet") or (pipeline.DOTNET if Path(pipeline.DOTNET).exists() else None),
         ".NET 10 SDK."),
        ("rsbuild", str(pipeline.RSBUILD) if pipeline.RSBUILD.exists() else None,
         "Compiled RsBuild (dotnet build src/RsBuild -c Release); built automatically the first time."),
        ("dlc", str(dlc_dir()) if dlc_dir().is_dir() else None,
         f"Rocksmith 2014 dlc/ folder ({dlc_dir()} does not exist): choose it in Settings."),
        ("tones", f"{len(_tone_index())} tones" if _tone_index() else None,
         "Tone library: without it a default tone is used."),
    ]
    busy = next((j.id for j in JOBS.values() if not j.finished), None)
    return {"checks": [{"key": k, "ok": bool(v), "value": v, "help": h} for k, v, h in checks],
            "busy_job": busy}


@app.get("/api/config")
def get_config():
    return {"dlc_dir": str(dlc_dir()), "default_dlc_dir": str(pipeline.DEFAULT_DLC_DIR),
            "dlc_dir_ok": dlc_dir().is_dir()}


@app.post("/api/config")
def set_config(dlc_dir_path: str = Form(..., alias="dlc_dir")):
    """Changes the game's dlc/ folder. It must exist; empty reverts to the default Steam one."""
    path = dlc_dir_path.strip().strip('"')
    if path and not Path(path).is_dir():
        raise HTTPException(400, f"Folder does not exist: {path}")
    if path and not (Path(path).name.lower() == "dlc" or any(Path(path).glob("*.psarc"))):
        raise HTTPException(400, "This does not look like the Rocksmith 2014 dlc/ folder (it is not named dlc and has no .psarc files).")
    pipeline.save_config(dlc_dir=path or None)
    return get_config()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
