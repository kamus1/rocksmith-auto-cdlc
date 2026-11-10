"""CDLC generator logic (audio + Guitar Pro tab → .psarc), with no UI.

Used by the CLI (cli.py) and the local web app. build_song reports progress through on_step(n, title) and
on_log(line) instead of printing, and signals errors with PipelineError instead of exiting the process.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import align, audio

ROOT = Path(__file__).resolve().parent.parent
RSBUILD = ROOT / "src" / "RsBuild" / "bin" / "Release" / "net10.0" / "RsBuild.dll"
DOTNET = shutil.which("dotnet") or r"C:\Program Files\dotnet\dotnet.exe"
DEFAULT_DLC_DIR = (Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\dlc") if os.name == "nt"
                   else Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common" / "Rocksmith2014" / "dlc")
WEM_DIR = ROOT / "external" / "wem-encoder"          # .wem encoder (submodule)
WAV2WEM = WEM_DIR / "wav2wem.py"
WEM_ENCODER = WEM_DIR / "build" / ("encode.exe" if os.name == "nt" else "encode")
# Windows: encode.exe comes from the wem-encoder release (built by its GitHub Actions), checked by sha256
WEM_RELEASE = "v0.1.0"
WEM_EXE_URL = f"https://github.com/kamus1/wem-encoder/releases/download/{WEM_RELEASE}/encode.exe"
WEM_EXE_SHA256 = "43087d01ba8ab0983e23b32cc2fdcc65ab82792e2f0c1b46a5f940da940f4919"
CONFIG_PATH = ROOT / "config.json"                  # user settings (not in git)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(**changes) -> dict:
    cfg = {**load_config(), **changes}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding="utf-8")
    return cfg


def dlc_dir() -> Path:
    """Rocksmith's dlc/ folder: the one in config.json or, if none, the default Steam one."""
    return Path(load_config().get("dlc_dir") or DEFAULT_DLC_DIR)
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
TONES_DIR = ROOT / "build" / "tones"
STEPS = 5                                            # steps numbered 0..STEPS


class PipelineError(Exception):
    """Error with a message for the user; detail holds the tool's full output, if any."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def tone_count() -> int:
    path = TONES_DIR / "index.json"
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return 0


def builtin_encoder() -> str | None:
    """The .wem encoder binary, or how it will be obtained; None if it cannot be used here."""
    if WEM_ENCODER.exists():
        return str(WEM_ENCODER)
    if os.name == "nt":
        return f"downloaded on first use (wem-encoder {WEM_RELEASE})"
    if shutil.which("cc") and shutil.which("make"):
        return "built on first use (external/wem-encoder/build.sh)"
    return None


def ensure_wem_encoder(on_log: Callable[[str], None] = print) -> None:
    """Windows: downloads encode.exe from the wem-encoder release if missing (Linux builds it in wav2wem.py)."""
    if os.name != "nt" or WEM_ENCODER.exists():
        return
    import hashlib
    import urllib.request
    on_log(f"Downloading encode.exe (wem-encoder {WEM_RELEASE})")
    try:
        with urllib.request.urlopen(WEM_EXE_URL, timeout=60) as resp:
            data = resp.read()
    except OSError as e:
        raise PipelineError(f"Could not download encode.exe from {WEM_EXE_URL}: {e}")
    if hashlib.sha256(data).hexdigest() != WEM_EXE_SHA256:
        raise PipelineError(f"encode.exe from {WEM_EXE_URL} does not match its expected sha256")
    WEM_ENCODER.parent.mkdir(parents=True, exist_ok=True)
    WEM_ENCODER.write_bytes(data)


def environment_checks() -> list[tuple[str, str | None, str, bool]]:
    """(key, value found or None, how to fix it, required) for each thing the build needs."""
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg", path=str(WINGET_LINKS))
    dotnet = shutil.which("dotnet") or (DOTNET if Path(DOTNET).exists() else None)
    tones = tone_count()
    return [
        ("encoder", builtin_encoder(),
         ".wem encoder (wem-encoder): on Linux it is built automatically (needs a C compiler, make and libogg); "
         "on Windows encode.exe is downloaded from the wem-encoder release.", True),
        ("ffmpeg", ffmpeg, "winget install Gyan.FFmpeg", True),
        ("dotnet", dotnet, ".NET 10 SDK: winget install Microsoft.DotNet.SDK.10", True),
        ("rsbuild", str(RSBUILD) if RSBUILD.exists() else None,
         "Compiled RsBuild: dotnet build src/RsBuild -c Release (also built automatically the first time).", False),
        ("dlc", str(dlc_dir()) if dlc_dir().is_dir() else None,
         f"Rocksmith 2014 dlc/ folder ({dlc_dir()} does not exist): set it in the web app's Settings.", True),
        ("tones", f"{tones} tones" if tones else None,
         "Tone library (optional): run \"cdlc tones\"; without it a default tone is used.", False),
    ]


def rsbuild(*args: str, env: dict | None = None) -> str:
    if not RSBUILD.exists():
        subprocess.run([DOTNET, "build", str(ROOT / "src" / "RsBuild"), "-c", "Release", "-v", "q"], check=True)
    p = subprocess.run([DOTNET, str(RSBUILD), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if p.returncode != 0:
        raise PipelineError(f"RsBuild {args[0]} failed (exit code {p.returncode})", p.stdout + p.stderr)
    return p.stdout


TONE_CATEGORIES = {
    "clean": ["Clean"],
    "overdrive": ["Overdrive", "Lead Overdrive", "Overdrive Lead"],
    "crunch": ["Overdrive", "Distortion"],
    "distortion": ["Distortion"],
    "highgain": ["High Gain", "Distortion High Gain"],
    "acoustic": ["Acoustic"],
    "fuzz": ["Fuzz"],
}

# Tone-name hints; "limpi" and "acu" also cover Spanish-named tones
CATEGORY_NAME_HINT = {
    "clean": re.compile(r"clean|limpi", re.I),
    "overdrive": re.compile(r"overdrive|drive|od|crunch", re.I),
    "crunch": re.compile(r"crunch|drive", re.I),
    "distortion": re.compile(r"dist", re.I),
    "highgain": re.compile(r"gain|metal|heavy", re.I),
    "acoustic": re.compile(r"acous|acu", re.I),
    "fuzz": re.compile(r"fuzz", re.I),
}

# Track's General MIDI instrument (Guitar Pro) → tone category
PROGRAM_CATEGORY = {24: "acoustic", 25: "acoustic", 26: "clean", 27: "clean", 28: "clean",
                    29: "overdrive", 30: "distortion", 31: "clean"}


def resolve_tone(tone: str | None, artist: str | None, title: str | None,
                 program: int | None = None) -> tuple[Path | None, str]:
    """Picks the tone: file, category or automatic (same song → same artist → track instrument)."""
    if tone and Path(tone).is_file():
        return Path(tone).resolve(), f"file {tone}"
    index_path = TONES_DIR / "index.json"
    if not index_path.exists():
        if tone:
            raise PipelineError("No tone library: run `python -m cdlc tones` first.")
        return None, "default tone (no library)"
    entries = json.loads(index_path.read_text(encoding="utf-8"))

    def pick(cands: list[dict]) -> dict | None:
        if not cands:
            return None
        # Prefer distortion/overdrive in the Lead arrangement, then the rest in a stable order
        rank = {"Distortion": 0, "High Gain": 0, "Overdrive": 1, "Lead": 2, "Clean": 3}
        return sorted(cands, key=lambda e: (rank.get(e["Descriptors"], 4), e["Arrangement"] != "Lead", e["File"]))[0]

    if tone:
        wanted = TONE_CATEGORIES.get(tone.lower())
        if wanted is None:
            raise PipelineError(f"The tone must be a file or a category: {', '.join(TONE_CATEGORIES)}")
        name_hint = CATEGORY_NAME_HINT[tone.lower()]
        # "acu" and "limpi" also cover Spanish-named tones
        contradicts = re.compile(r"clean|acoust|acu|limpi" if tone.lower() not in ("clean", "acoustic")
                                 else r"dist|drive|od|fuzz|gain|lead", re.I)
        cands = [e for e in entries if e["Descriptors"] in wanted and not contradicts.search(e["Name"])]
        chosen = sorted(cands, key=lambda e: (not name_hint.search(e["Name"]), e["Descriptors"] != wanted[0],
                                              e["Arrangement"] != "Lead", e["File"]))[0] if cands else None
        if chosen is None:
            raise PipelineError(f"No tones of category {tone} in the library")
        return TONES_DIR / chosen["File"], f"category {tone}: {chosen['Song']} / {chosen['Name']}"

    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    if artist and title:
        same_song = [e for e in entries if norm(e["Song"]) == norm(f"{artist} - {title}")]
        if (c := pick(same_song)):
            return TONES_DIR / c["File"], f"same song: {c['Song']} / {c['Name']} ({c['Descriptors']})"
    if artist:
        same_artist = [e for e in entries if norm(e["Song"].split(" - ")[0]) == norm(artist)]
        if (c := pick(same_artist)):
            return TONES_DIR / c["File"], f"same artist: {c['Song']} / {c['Name']} ({c['Descriptors']})"
    if program in PROGRAM_CATEGORY:
        path, desc = resolve_tone(PROGRAM_CATEGORY[program], None, None)
        return path, f"from the track instrument (MIDI {program}), {desc}"
    return None, "default tone"


# If the source song lacks a sound, the closest one it does have (so sources are not mixed)
CATEGORY_FALLBACK = {
    "clean": ["acoustic", "overdrive"], "acoustic": ["clean"], "overdrive": ["crunch", "distortion", "clean"],
    "crunch": ["overdrive", "distortion"], "distortion": ["highgain", "crunch", "overdrive"],
    "highgain": ["distortion"], "fuzz": ["distortion", "overdrive"],
}


def tone_set(categories: list[str], artist: str | None, title: str | None,
             program: int | None = None) -> dict[str, tuple[Path, str]]:
    """One tone per sound of the track, all from the same library song: a song's tones are balanced
    against each other (volume, gain) and those of different songs are not (an overdrive from another
    song can sound much quieter than the distortion). Order: the same song; one by the same artist
    (filling missing sounds with the closest one, if at least two distinct tones remain); one that has
    all the sounds.
    Returns {category: (file, description)}; empty if there is no library or no song fits."""
    index_path = TONES_DIR / "index.json"
    cats = [c for c in categories if c in TONE_CATEGORIES]
    if not index_path.exists() or len(cats) < 2:
        return {}
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    by_song: dict[str, list[dict]] = {}
    for e in entries:
        by_song.setdefault(e["Song"], []).append(e)

    def assign(song: str) -> tuple[dict[str, dict], int]:
        """The song's tones for each sound, and how many are of their exact category."""
        out, exact = {}, 0
        for c in cats:
            for i, cand in enumerate([c] + CATEGORY_FALLBACK.get(c, [])):
                wanted = TONE_CATEGORIES[cand]
                hits = sorted((e for e in by_song[song] if e["Descriptors"] in wanted),
                              key=lambda e: (e["Descriptors"] != wanted[0], e["Arrangement"] != "Lead", e["File"]))
                if hits:
                    out[c] = hits[0]
                    exact += i == 0
                    break
        return out, exact

    same_song = [s for s in by_song if artist and title and norm(s) == norm(f"{artist} - {title}")]
    same_artist = [s for s in by_song if artist and norm(s.split(" - ")[0]) == norm(artist)]
    for label, songs, need_exact in (("same song", same_song, False), ("same artist", same_artist, False),
                                     ("another song with all the sounds", sorted(by_song), True)):
        best = None
        for song in songs:
            got, exact = assign(song)
            # Some contrast must remain: if every sound ends up on the same tone, it is useless
            distinct = len({e["File"] for e in got.values()})
            if len(got) == len(cats) and distinct >= 2 and (not need_exact or exact == len(cats)):
                if best is None or exact > best[2]:
                    best = (song, got, exact)
        if best:
            song, got, _ = best
            return {c: (TONES_DIR / e["File"], f"{label}: {song} / {e['Name']} ({e['Descriptors']})"
                        + ("" if e["Descriptors"] in TONE_CATEGORIES[c] else f", instead of {c}"))
                    for c, e in got.items()}
    return {}


# Track-name hints; "ritm", "r[ií]tmica" and "voz" also cover Spanish-named tracks
LEAD_HINT = re.compile(r"lead|solo|hammett|slash|gilmour", re.I)
RHYTHM_HINT = re.compile(r"rh?yth?m|ritm|r[ií]tmica|hetfield|izzy", re.I)
NOT_GUITAR = re.compile(r"voice|vocal|voz|melod|keys|piano|synth|organ|strings", re.I)


# Words that say nothing about which song a file is
NAME_NOISE = {"the", "a", "an", "and", "of", "de", "la", "el", "los", "las", "y", "feat", "ft", "audio", "official",
              "video", "music", "lyrics", "lyric", "hd", "hq", "remaster", "remastered", "version", "live", "mp3",
              "song", "track", "full", "album", "kbps", "1080p", "720p", "128k", "320k", "ver", "by", "tab"}


def name_words(text: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.split(r"[^a-z0-9]+", t) if len(w) >= 2 and not w.isdigit() and w not in NAME_NOISE
            and not re.fullmatch(r"\d+k|\d+p|v\d+", w)}


def names_differ(artist: str | None, title: str | None, audio_file: Path) -> bool:
    """True if the tab's artist and title share no word with the audio's file name and tags.
    False when there is nothing to compare (tab without a title, audio named "song.mp3"...)."""
    tab_words = name_words(title) | name_words(artist)
    tg = audio.tags(audio_file)
    audio_words = name_words(audio_file.stem) | name_words(tg.get("title")) | name_words(tg.get("artist"))
    return bool(name_words(title)) and bool(audio_words) and not (tab_words & audio_words)


def choose_tracks(info: dict) -> tuple[int, int | None, str]:
    """Picks the tab's Lead and Rhythm tracks. Returns (lead, rhythm, explanation)."""
    bars = max(1, info["bars"])
    guitars = [t for t in info["tracks"]
               if t["strings"] in (6, 7) and not t["is_percussion"] and t["bars_with_notes"] >= 0.1 * bars
               and not NOT_GUITAR.search(t["name"])]
    if not guitars:
        # No guitars: use the pitched track covering the most bars (e.g. a bass played on guitar)
        pitched = [t for t in info["tracks"] if not t["is_percussion"] and t["bars_with_notes"] > 0]
        if not pitched:
            raise PipelineError("The tab has no tracks with notes.")
        best = max(pitched, key=lambda t: t["bars_with_notes"])
        return best["index"], None, f"Lead = {best['name']} (no guitar tracks)"

    cover = lambda t: t["bars_with_notes"] / bars
    by_cover = sorted(guitars, key=lambda t: -t["bars_with_notes"])
    lead_hint = [t for t in guitars if LEAD_HINT.search(t["name"]) and cover(t) >= 0.4]
    rhythm_hint = [t for t in guitars if RHYTHM_HINT.search(t["name"])]

    if lead_hint:
        lead = max(lead_hint, key=lambda t: t["bars_with_notes"])
        why = "by name"
    else:
        major = [t for t in by_cover if cover(t) >= 0.5]
        if len(major) >= 2 and not (rhythm_hint and rhythm_hint[0] in major[:2]):
            lead = max(major[:2], key=lambda t: t["mean_fret"])
            why = "the higher-fretted of the two main ones"
        elif rhythm_hint and len(major) >= 2:
            lead = next(t for t in major if t is not rhythm_hint[0])
            why = "the main one that is not rhythm"
        else:
            lead = by_cover[0]
            why = "the one covering the most bars"
    rest = [t for t in by_cover if t is not lead and cover(t) >= 0.25]
    rhythm_cands = [t for t in rest if t in rhythm_hint] or rest
    rhythm = rhythm_cands[0] if rhythm_cands else None
    desc = f"Lead = {lead['name']} ({why})"
    if rhythm:
        desc += f", Rhythm = {rhythm['name']}"
    return lead["index"], (rhythm["index"] if rhythm else None), desc


def lookup_song(artist: str, title: str, on_log: Callable[[str], None] = print) -> dict:
    """Looks up album, year and cover in the public iTunes API. Returns {} if nothing is found."""
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    try:
        with urllib.request.urlopen(f"https://itunes.apple.com/search?{query}", timeout=10) as resp:
            results = json.loads(resp.read().decode("utf-8")).get("results", [])
    except OSError as e:
        on_log(f"(cannot reach iTunes: {e})")
        return {}
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    for r in results:
        if norm(r.get("artistName")) == norm(artist) and norm(title) in norm(r.get("trackName")):
            return {
                "album": r.get("collectionName"),
                "year": int(r["releaseDate"][:4]) if r.get("releaseDate") else None,
                "cover_url": (r.get("artworkUrl100") or "").replace("100x100bb", "600x600bb") or None,
            }
    return {}


def download_cover(url: str, dest: Path, on_log: Callable[[str], None] = print) -> Path | None:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            dest.write_bytes(resp.read())
        return dest
    except OSError as e:
        on_log(f"(could not download the cover: {e})")
        return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "song"


def deploy(psarc: Path, on_log: Callable[[str], None] = print) -> Path:
    """Copies the .psarc to dlc/ with a new version number and deletes the previous versions.

    The game does not reread a file replaced under the same name while it is running
    (the song disappears when selected), but it does detect new files when returning to the title screen.
    """
    key = re.sub(r"_v\d+_p\.psarc$", "", psarc.name)
    pattern = re.compile(rf"^{re.escape(key)}_v(\d+)_p\.psarc$")
    target_dir = dlc_dir()
    if not target_dir.is_dir():
        raise PipelineError(f"The game folder does not exist: {target_dir}. Choose it in Settings.")
    old = [p for p in target_dir.glob(f"{key}_v*_p.psarc") if pattern.match(p.name)]
    # The package's version (shown in the in-game title) if it is new; otherwise the next free one
    own = re.search(r"_v(\d+)_p\.psarc$", psarc.name)
    version = max(max((int(pattern.match(p.name).group(1)) for p in old), default=0) + 1,
                  int(own.group(1)) if own else 0)
    target = target_dir / f"{key}_v{version}_p.psarc"
    shutil.copy2(psarc, target)
    for p in old:
        try:
            p.unlink()
        except OSError as e:
            on_log(f"Could not delete {p.name} ({e}); delete it with the game closed.")
    return target


TIMINGS = ("auto", "sync-points", "align")


def extract_backing_track(tab: Path, out_base: Path) -> Path:
    """Saves the tab's embedded audio (GP8 backing track) as out_base.<ext> and returns its path."""
    return Path(rsbuild("backing-track", str(tab), str(out_base)).strip().splitlines()[-1])


def inspect_tab(tab: Path) -> dict:
    """The tab's tracks and metadata (RsBuild inspect-json)."""
    return json.loads(rsbuild("inspect-json", str(Path(tab).resolve())))


def build_tones(dlc: Path | None = None) -> str:
    """Extracts the tone library from the installed CDLC into build/tones."""
    return rsbuild("tones", str(Path(dlc or dlc_dir()).resolve()), str(TONES_DIR)).strip()


COMMON_FRETS = 22                   # many guitars have 21 or 22 frets; Rocksmith allows up to 24


def high_fret_notes(pkg: Path, bar_times_ms: list[float]) -> list[str]:
    """Notes above fret COMMON_FRETS in the generated arrangements: a possible tab error
    (or a note many guitars cannot play). One line per arrangement with bar and minute."""
    import bisect
    import xml.etree.ElementTree as ET

    out = []
    for xml in sorted(pkg.glob("arr_*_RS2.xml")):
        root = ET.parse(xml).getroot()
        found = []
        for level in root.find("levels"):
            for n in level.find("notes"):
                found.append((float(n.get("time")), int(n.get("fret"))))
            for c in level.find("chords"):
                found += [(float(c.get("time")), int(cn.get("fret"))) for cn in c.findall("chordNote")]
        found = sorted({(t, f) for t, f in found if f > COMMON_FRETS})
        if found:
            where = [f"fret {f} in bar {bisect.bisect_right(bar_times_ms, t * 1000)} "
                     f"({int(t // 60)}:{t % 60:04.1f})" for t, f in found[:8]]
            more = f" and {len(found) - 8} more" if len(found) > 8 else ""
            kind = xml.stem.removeprefix("arr_").removesuffix("_RS2").capitalize()
            out.append(f"{kind}: {len(found)} notes above fret {COMMON_FRETS} (many guitars cannot "
                       f"reach them; may be a tab error): {', '.join(where)}{more}")
    return out


@dataclass
class BuildOptions:
    audio: Path | None                  # None: the tab's embedded audio (Guitar Pro 8 backing track)
    tab: Path
    lead: int | None = None             # None: automatic choice
    rhythm: int | None = None           # None: automatic (unless no_rhythm)
    no_rhythm: bool = False
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    year: int | None = None
    cover: str | None = None
    tone: str | None = None             # file or category; None: automatic
    name: str | None = None             # folder in build/
    variant: str | None = None          # variant: own title and key, coexists with the normal version
    pad: float = 4.0                    # seconds of silence at the start (count-in)
    timing: str = "auto"                # bar times: auto (GP8 sync points if usable, else align),
                                        # sync-points or align
    deploy: bool = True
    lookup: bool = True                 # album, year and cover from iTunes


@dataclass
class BuildResult:
    out_dir: Path
    psarc: Path
    report: Path
    deployed: Path | None
    cover: Path | None
    meta: dict
    tracks: str
    tone: str
    alignment: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)   # warnings for the user (also in the report)


def build_song(opts: BuildOptions, on_step: Callable[[int, str], None] = lambda n, t: None,
               on_log: Callable[[str], None] = lambda line: None) -> BuildResult:
    """Generates the CDLC. on_step(n, title) marks the start of each step (0..STEPS); on_log, each detail line."""
    if WINGET_LINKS.is_dir() and str(WINGET_LINKS) not in os.environ["PATH"]:
        os.environ["PATH"] = os.environ["PATH"] + os.pathsep + str(WINGET_LINKS)
    if builtin_encoder() is None:
        raise PipelineError("The .wem encoder cannot be built: it needs a C compiler, make and libogg "
                            "(see external/wem-encoder/README.md).")

    tab = Path(opts.tab).resolve()
    if not tab.is_file():
        raise PipelineError(f"Tab file does not exist: {tab}")
    audio_in = Path(opts.audio).resolve() if opts.audio else None
    if audio_in is not None and not audio_in.is_file():
        raise PipelineError(f"Audio file does not exist: {audio_in}")
    if opts.timing not in TIMINGS:
        raise PipelineError(f"Unknown timing: {opts.timing} (use {', '.join(TIMINGS)})")
    log_lines = lambda text: [on_log(line) for line in text.strip().splitlines()]

    # Metadata and tracks: whatever the options do not give comes from the tab
    info = inspect_tab(tab)
    n_sync, has_backing = info.get("sync_point_count", 0), info.get("has_backing_track", False)
    if audio_in is None and not has_backing:
        raise PipelineError("No audio given and the tab has no embedded audio (Guitar Pro 8 backing track).")
    if opts.timing == "sync-points" and n_sync == 0:
        raise PipelineError("The tab has no sync points (Guitar Pro 8): use --timing auto or align.")
    meta = {"artist": opts.artist or info["artist"] or "Unknown",
            "title": opts.title or info["title"] or tab.stem,
            "album": opts.album or info["album"] or "",
            "year": opts.year, "cover": opts.cover}
    if opts.lead is None:
        lead, auto_rhythm, tracks_desc = choose_tracks(info)
        rhythm = opts.rhythm if opts.rhythm is not None else (None if opts.no_rhythm else auto_rhythm)
        if opts.no_rhythm and auto_rhythm is not None:
            tracks_desc = tracks_desc.split(", Rhythm")[0]
    else:
        lead, rhythm = opts.lead, (None if opts.no_rhythm else opts.rhythm)
        tracks_desc = f"Lead = track {lead}" + (f", Rhythm = track {rhythm}" if rhythm is not None else "")
    # Tab and audio of different songs (the alignment would still produce something): warn early
    name_warning = None
    if audio_in is not None and info["title"] and names_differ(info["artist"], info["title"], audio_in):
        name_warning = (f"The tab ({info['artist'] or '?'} - {info['title']}) and the audio ({audio_in.name}) "
                        "seem to be different songs. Check them.")
        on_log(f"WARNING: {name_warning}")
    out = ROOT / "build" / (opts.name or slug(f"{meta['artist']} {meta['title']} {opts.variant or ''}"))
    out.mkdir(parents=True, exist_ok=True)
    on_step(0, f"{meta['artist']} - {meta['title']} | {tracks_desc}")
    backing = None
    if audio_in is None:
        backing = audio_in = extract_backing_track(tab, out / "backing")
        on_log(f"Audio: the tab's embedded backing track ({audio_in.name})")

    if opts.lookup and (meta["year"] is None or meta["cover"] is None or not meta["album"]):
        found = lookup_song(meta["artist"], meta["title"], on_log)
        meta["album"] = meta["album"] or found.get("album") or ""
        meta["year"] = meta["year"] or found.get("year")
        if meta["cover"] is None and found.get("cover_url"):
            cover = download_cover(found["cover_url"], out / "cover.jpg", on_log)
            meta["cover"] = str(cover) if cover else None
        if found:
            on_log(f"iTunes: {meta['album']} ({meta['year']}), cover {'yes' if meta['cover'] else 'no'}")

    on_step(1, f"Audio → WAV (+{opts.pad:g} s of silence)")
    try:
        wav = audio.to_wav(audio_in, out / "song.wav", pad_seconds=opts.pad)
    except subprocess.CalledProcessError as e:
        raise PipelineError("ffmpeg could not convert the audio (damaged file or unsupported format?)",
                            (e.stderr or b"").decode("utf-8", "replace")) from e
    except RuntimeError as e:                       # audio.ffmpeg_exe: not on the PATH
        raise PipelineError("ffmpeg not found. Install it (winget install Gyan.FFmpeg) and try again.") from e

    on_step(2, "Tab timeline")
    log_lines(rsbuild("timeline", str(tab), str(out / "timeline.json")))

    warnings = [name_warning] if name_warning else []
    sync_offset = None
    step3_shown = False
    if opts.timing != "align" and n_sync >= (1 if opts.timing == "sync-points" else 2) and has_backing:
        on_step(3, f"Bar times: the tab has Guitar Pro 8 sync points ({n_sync})")
        step3_shown = True
        if backing is not None:
            sync_offset, source = opts.pad * 1000, "the tab's own audio"
        else:
            # The points refer to the embedded audio: usable only if the given audio is the same recording
            ref = audio.to_wav(extract_backing_track(tab, out / "backing"), out / "backing.wav")
            m = align.audio_offset(ref, wav)
            on_log(f"Your audio vs the tab's: correlation {m['correlation']}, drift {m['drift_ms']} ms, "
                   f"offset {m['offset_ms']} ms")
            if m["same_recording"]:
                sync_offset, source = m["offset_ms"], "your audio, same recording as the tab's"
            elif opts.timing == "sync-points":
                # Not the same recording: the offset found means nothing; assume both start together
                sync_offset, source = opts.pad * 1000, "your audio (forced), assumed to start like the tab's"
                warnings.append("The audio does not look like the tab's recording, but its sync points were used "
                                "(--timing sync-points), assuming both start together: check the sync.")
            if sync_offset is None:
                on_log("Your audio is a different recording from the tab's: aligning instead of using its sync points.")
    if sync_offset is not None:
        r = align.bar_times_from_sync_points(out / "timeline.json", wav, out / "bar_times.json", sync_offset)
        bpm_note = (f"Timing: Guitar Pro 8 sync points, {n_sync} points, offset {sync_offset:.0f} ms ({source}); "
                    f"bar 1 at {r['bar_times_ms'][0] / 1000:.2f} s")
    else:
        (on_log if step3_shown else lambda t: on_step(3, t))("Tab ↔ audio alignment (harmony + drums + tempo)")
        r = align.align_hybrid(out / "timeline.json", wav, out / "bar_times.json")
        bpm_note = (f"bar 1 at {r['bar_times_ms'][0] / 1000:.2f} s, tempo ratio {r['tempo_ratio_median']}, "
                    f"mean correction over harmony: {r['mean_abs_offset_ms']} ms, "
                    f"bars with odd tempo: {len(r['suspicious_bars'])}")
    on_log(bpm_note)
    tl = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    synced = r["method"] == "sync_points"
    # Signs of a tab from another song (thresholds: 23 right pairs vs 10 crossed ones, see docs/ALIGNMENT.md)
    kept = (r.get("tail_cut_bar") or len(r["bar_times_ms"])) - 1
    odd = len(r["suspicious_bars"]) / max(kept, 1)
    # With sync points the tempo is the author's: only the harmony says whether tab and audio match
    signs = ([f"little harmony in common ({r['chroma_match']})"] if r["chroma_match"] < 0.025 else []) + \
            ([f"tempo ×{r['tempo_ratio_median']:.2f} the tab's"]
             if not synced and not 0.85 <= r["tempo_ratio_median"] <= 1.18 else []) + \
            ([f"{odd:.0%} of bars off-tempo"] if odd > 0.5 else [])
    if signs:
        warnings.append(f"The tab may not match the audio ({', '.join(signs)}). Check they are the same song.")
    if not synced and not any(n[2] < 0 for n in tl["notes"]):
        warnings.append("The tab has no drums: fine alignment may be less accurate.")
    for k, sec in r.get("gaps", []):
        warnings.append(f"The recording has {sec:.1f} s not in the tab before bar {k}: that stretch has no notes.")
    if r.get("tail_cut_bar"):
        warnings.append(f"The recording ends before the tab (fade-out?): bars {r['tail_cut_bar']} to "
                        f"{len(r['bar_times_ms']) - 1} are left out.")
    if r["suspicious_bars"]:
        warnings.append(f"{len(r['suspicious_bars'])} bars with a tempo very different from the rest (may be off): "
                        + ", ".join(f"b{k}" for k in r["suspicious_bars"][:12]) + ("…" if len(r["suspicious_bars"]) > 12 else ""))
    for w in warnings:
        if w is not name_warning:                   # already shown before the alignment
            on_log(f"WARNING: {w}")

    lead_program = next((t["program"] for t in info["tracks"] if t["index"] == lead), None)
    tone_path, tone_desc = resolve_tone(opts.tone, meta["artist"], meta["title"], lead_program)
    # Tracks that change sound (instrument or tab texts): one tone per sound, unless a specific tone
    # was requested
    per_track_tones: dict[str, dict[str, Path]] = {}
    tone_lines = []
    for kind, idx in (("lead", lead), ("rhythm", rhythm)):
        track = next((t for t in info["tracks"] if t["index"] == idx), None) if idx is not None else None
        cats = (track or {}).get("tone_categories", [])
        if opts.tone or len(cats) < 2:
            continue
        chosen = tone_set(cats, meta["artist"], meta["title"], track["program"])
        if len(chosen) >= 2:
            per_track_tones[kind] = {c: path for c, (path, _desc) in chosen.items()}
            tone_lines += [f"{kind.capitalize()}, {c}: {desc}" for c, (_path, desc) in chosen.items()]
    if per_track_tones:
        tone_desc += "; with tone changes: " + "; ".join(tone_lines)
    on_step(4, f"XML, audio and packaging — tone: {tone_desc}")
    args = ["build", "--tab", str(tab), "--lead", str(lead), "--bar-times", str(out / "bar_times.json"),
            "--wav", str(wav), "--out-dir", str(out / "pkg")]
    if rhythm is not None:
        args += ["--rhythm", str(rhythm)]
    for key in ("artist", "title", "album", "year", "cover"):
        value = meta[key]
        if value:
            args += [f"--{key}", str(value)]
    if tone_path:
        args += ["--tone", str(tone_path)]
    if dlc_dir().is_dir():
        args += ["--dlc-dir", str(dlc_dir())]        # next version after the game's ones, in the title
    if opts.variant:
        args += ["--variant", opts.variant]
    for kind, chosen in per_track_tones.items():
        args += [f"--tones-{kind}", "|".join(f"{c}={p}" for c, p in chosen.items())]
    ensure_wem_encoder(on_log)
    args += ["--wem-cmd", f"{sys.executable} {WAV2WEM}"]
    log = rsbuild(*args)
    log_lines(log)
    m = re.search(r"PSARC:\s+(.+)", log)
    if not m:
        raise PipelineError("RsBuild did not report the generated .psarc", log)
    psarc = Path(m.group(1).strip())

    high = high_fret_notes(out / "pkg", r["bar_times_ms"])
    for line in high:
        on_log(f"WARNING: {line}")
    warnings += high

    report = out / "pkg" / "report.md"
    with report.open("a", encoding="utf-8") as f:
        if warnings:
            f.write("\n## Warnings\n" + "".join(f"- {line}\n" for line in warnings))
        f.write(f"\n## Tracks\n- {tracks_desc}\n")
        f.write(f"\n## Tone\n- {tone_desc}\n")
        f.write(f"\n## Alignment\n- {bpm_note}\n")
        if r["suspicious_bars"]:
            f.write(f"- Bars with odd tempo: {r['suspicious_bars'][:40]}\n")
        if r.get("gaps"):
            f.write(f"- Audio not in the tab (bar, seconds): {r['gaps']}\n")
        if r.get("tail_cut_bar"):
            f.write(f"- The recording ends before the tab: bars from {r['tail_cut_bar']} on left out\n")

    deployed = None
    if opts.deploy:
        deployed = deploy(psarc, on_log)
        on_step(5, f"Copied to {deployed}")
        on_log("Go back to the game's title screen so it loads it.")
    else:
        on_step(5, f"Not copied to dlc/: {psarc}")
    return BuildResult(out_dir=out, psarc=psarc, report=report, deployed=deployed,
                       cover=Path(meta["cover"]) if meta["cover"] else None, meta=meta,
                       tracks=tracks_desc, tone=tone_desc, alignment=r, warnings=warnings)
