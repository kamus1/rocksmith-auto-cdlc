"""Lógica del generador de CDLC (audio + tab de Guitar Pro → .psarc), sin interfaz.

La usan la CLI (cli.py) y la web local. build_song informa del progreso con on_step(n, título) y
on_log(línea) en lugar de imprimir, y señala los errores con PipelineError en vez de terminar el proceso.
"""

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import align, audio

ROOT = Path(__file__).resolve().parent.parent
RSBUILD = ROOT / "src" / "RsBuild" / "bin" / "Release" / "net10.0" / "RsBuild.dll"
DOTNET = shutil.which("dotnet") or r"C:\Program Files\dotnet\dotnet.exe"
DLC_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\dlc")
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
SUPPORTED_WWISE = re.compile(r"20(19|21|22|23)")
TONES_DIR = ROOT / "build" / "tones"
STEPS = 5                                            # pasos numerados 0..STEPS


class PipelineError(Exception):
    """Error con un mensaje para el usuario; detail lleva la salida completa de la herramienta, si la hay."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def find_wwise() -> str | None:
    """Carpeta de una instalación de Wwise compatible (2019–2023)."""
    env = os.environ.get("WWISEROOT")
    if env and SUPPORTED_WWISE.search(env) and Path(env).is_dir():
        return env
    pattern = "WwiseConsole.exe"
    roots = [r"C:\Audiokinetic", r"C:\Program Files (x86)\Audiokinetic", r"C:\Program Files\Audiokinetic"]
    for root in roots:
        for d in sorted(glob.glob(os.path.join(root, "*")), reverse=True):
            if SUPPORTED_WWISE.search(os.path.basename(d)) and \
                    Path(d, "Authoring", "x64", "Release", "bin", pattern).exists():
                return d
    return None


def rsbuild(*args: str, env: dict | None = None) -> str:
    if not RSBUILD.exists():
        subprocess.run([DOTNET, "build", str(ROOT / "src" / "RsBuild"), "-c", "Release", "-v", "q"], check=True)
    p = subprocess.run([DOTNET, str(RSBUILD), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if p.returncode != 0:
        raise PipelineError(f"RsBuild {args[0]} falló (código {p.returncode})", p.stdout + p.stderr)
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

CATEGORY_NAME_HINT = {
    "clean": re.compile(r"clean|limpi", re.I),
    "overdrive": re.compile(r"overdrive|drive|od|crunch", re.I),
    "crunch": re.compile(r"crunch|drive", re.I),
    "distortion": re.compile(r"dist", re.I),
    "highgain": re.compile(r"gain|metal|heavy", re.I),
    "acoustic": re.compile(r"acous|acu", re.I),
    "fuzz": re.compile(r"fuzz", re.I),
}

# Instrumento MIDI General de la pista (Guitar Pro) → categoría de tono
PROGRAM_CATEGORY = {24: "acoustic", 25: "acoustic", 26: "clean", 27: "clean", 28: "clean",
                    29: "overdrive", 30: "distortion", 31: "clean"}


def resolve_tone(tone: str | None, artist: str | None, title: str | None,
                 program: int | None = None) -> tuple[Path | None, str]:
    """Elige el tono: archivo, categoría o automático (misma canción → mismo artista → instrumento de la pista)."""
    if tone and Path(tone).is_file():
        return Path(tone).resolve(), f"archivo {tone}"
    index_path = TONES_DIR / "index.json"
    if not index_path.exists():
        if tone:
            raise PipelineError("No hay biblioteca de tonos: ejecuta primero `python -m cdlc tones`.")
        return None, "tono por defecto (sin biblioteca)"
    entries = json.loads(index_path.read_text(encoding="utf-8"))

    def pick(cands: list[dict]) -> dict | None:
        if not cands:
            return None
        # Preferir distorsión/overdrive en el arrangement Lead, después el resto en orden estable
        rank = {"Distortion": 0, "High Gain": 0, "Overdrive": 1, "Lead": 2, "Clean": 3}
        return sorted(cands, key=lambda e: (rank.get(e["Descriptors"], 4), e["Arrangement"] != "Lead", e["File"]))[0]

    if tone:
        wanted = TONE_CATEGORIES.get(tone.lower())
        if wanted is None:
            raise PipelineError(f"El tono debe ser un archivo o una categoría: {', '.join(TONE_CATEGORIES)}")
        name_hint = CATEGORY_NAME_HINT[tone.lower()]
        contradicts = re.compile(r"clean|acoust|acu|limpi" if tone.lower() not in ("clean", "acoustic")
                                 else r"dist|drive|od|fuzz|gain|lead", re.I)
        cands = [e for e in entries if e["Descriptors"] in wanted and not contradicts.search(e["Name"])]
        chosen = sorted(cands, key=lambda e: (not name_hint.search(e["Name"]), e["Descriptors"] != wanted[0],
                                              e["Arrangement"] != "Lead", e["File"]))[0] if cands else None
        if chosen is None:
            raise PipelineError(f"No hay tonos de la categoría {tone} en la biblioteca")
        return TONES_DIR / chosen["File"], f"categoría {tone}: {chosen['Song']} / {chosen['Name']}"

    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    if artist and title:
        same_song = [e for e in entries if norm(e["Song"]) == norm(f"{artist} - {title}")]
        if (c := pick(same_song)):
            return TONES_DIR / c["File"], f"misma canción: {c['Song']} / {c['Name']} ({c['Descriptors']})"
    if artist:
        same_artist = [e for e in entries if norm(e["Song"].split(" - ")[0]) == norm(artist)]
        if (c := pick(same_artist)):
            return TONES_DIR / c["File"], f"mismo artista: {c['Song']} / {c['Name']} ({c['Descriptors']})"
    if program in PROGRAM_CATEGORY:
        path, desc = resolve_tone(PROGRAM_CATEGORY[program], None, None)
        return path, f"según el instrumento de la pista (MIDI {program}), {desc}"
    return None, "tono por defecto"


LEAD_HINT = re.compile(r"lead|solo|hammett|slash|gilmour", re.I)
RHYTHM_HINT = re.compile(r"rhythm|rhytm|ritm|r[ií]tmica|hetfield|izzy", re.I)
NOT_GUITAR = re.compile(r"voice|vocal|voz|melod|keys|piano|synth|organ|strings", re.I)


def choose_tracks(info: dict) -> tuple[int, int | None, str]:
    """Elige las pistas Lead y Rhythm de la tab. Devuelve (lead, rhythm, explicación)."""
    bars = max(1, info["bars"])
    guitars = [t for t in info["tracks"]
               if t["strings"] in (6, 7) and not t["is_percussion"] and t["bars_with_notes"] >= 0.1 * bars
               and not NOT_GUITAR.search(t["name"])]
    if not guitars:
        # Sin guitarras: usar la pista con altura que más compases cubra (p. ej. un bajo tocado en guitarra)
        pitched = [t for t in info["tracks"] if not t["is_percussion"] and t["bars_with_notes"] > 0]
        if not pitched:
            raise PipelineError("La tab no tiene pistas con notas.")
        best = max(pitched, key=lambda t: t["bars_with_notes"])
        return best["index"], None, f"Lead = {best['name']} (no hay pistas de guitarra)"

    cover = lambda t: t["bars_with_notes"] / bars
    by_cover = sorted(guitars, key=lambda t: -t["bars_with_notes"])
    lead_hint = [t for t in guitars if LEAD_HINT.search(t["name"]) and cover(t) >= 0.4]
    rhythm_hint = [t for t in guitars if RHYTHM_HINT.search(t["name"])]

    if lead_hint:
        lead = max(lead_hint, key=lambda t: t["bars_with_notes"])
        why = "por nombre"
    else:
        major = [t for t in by_cover if cover(t) >= 0.5]
        if len(major) >= 2 and not (rhythm_hint and rhythm_hint[0] in major[:2]):
            lead = max(major[:2], key=lambda t: t["mean_fret"])
            why = "la de trastes más altos de las dos principales"
        elif rhythm_hint and len(major) >= 2:
            lead = next(t for t in major if t is not rhythm_hint[0])
            why = "la principal que no es rítmica"
        else:
            lead = by_cover[0]
            why = "la que más compases cubre"
    rest = [t for t in by_cover if t is not lead and cover(t) >= 0.25]
    rhythm_cands = [t for t in rest if t in rhythm_hint] or rest
    rhythm = rhythm_cands[0] if rhythm_cands else None
    desc = f"Lead = {lead['name']} ({why})"
    if rhythm:
        desc += f", Rhythm = {rhythm['name']}"
    return lead["index"], (rhythm["index"] if rhythm else None), desc


def lookup_song(artist: str, title: str, on_log: Callable[[str], None] = print) -> dict:
    """Busca álbum, año y portada en la API pública de iTunes. Devuelve {} si no hay resultado."""
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    try:
        with urllib.request.urlopen(f"https://itunes.apple.com/search?{query}", timeout=10) as resp:
            results = json.loads(resp.read().decode("utf-8")).get("results", [])
    except OSError as e:
        on_log(f"(sin conexión con iTunes: {e})")
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
        on_log(f"(no se pudo descargar la portada: {e})")
        return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "cancion"


def deploy(psarc: Path, on_log: Callable[[str], None] = print) -> Path:
    """Copia el .psarc a dlc/ con un número de versión nuevo y borra las versiones anteriores.

    El juego no relee un archivo que sustituimos con el mismo nombre mientras está abierto
    (la canción desaparece al elegirla), pero sí detecta archivos nuevos al volver al título.
    """
    key = re.sub(r"_v\d+_p\.psarc$", "", psarc.name)
    pattern = re.compile(rf"^{re.escape(key)}_v(\d+)_p\.psarc$")
    old = [p for p in DLC_DIR.glob(f"{key}_v*_p.psarc") if pattern.match(p.name)]
    version = max((int(pattern.match(p.name).group(1)) for p in old), default=0) + 1
    target = DLC_DIR / f"{key}_v{version}_p.psarc"
    shutil.copy2(psarc, target)
    for p in old:
        try:
            p.unlink()
        except OSError as e:
            on_log(f"No se pudo borrar {p.name} ({e}); bórralo con el juego cerrado.")
    return target


def inspect_tab(tab: Path) -> dict:
    """Pistas y metadatos de la tab (RsBuild inspect-json)."""
    return json.loads(rsbuild("inspect-json", str(Path(tab).resolve())))


def build_tones(dlc: Path = DLC_DIR) -> str:
    """Extrae la biblioteca de tonos de los CDLC instalados en build/tones."""
    return rsbuild("tones", str(Path(dlc).resolve()), str(TONES_DIR)).strip()


@dataclass
class BuildOptions:
    audio: Path
    tab: Path
    lead: int | None = None             # None: elección automática
    rhythm: int | None = None           # None: automática (salvo no_rhythm)
    no_rhythm: bool = False
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    year: int | None = None
    cover: str | None = None
    tone: str | None = None             # archivo o categoría; None: automático
    name: str | None = None             # carpeta en build/
    pad: float = 4.0                    # segundos de silencio al inicio (count-in)
    deploy: bool = True
    lookup: bool = True                 # álbum, año y portada desde iTunes


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


def build_song(opts: BuildOptions, on_step: Callable[[int, str], None] = lambda n, t: None,
               on_log: Callable[[str], None] = lambda line: None) -> BuildResult:
    """Genera el CDLC. on_step(n, título) marca el inicio de cada paso (0..STEPS); on_log, cada línea de detalle."""
    if WINGET_LINKS.is_dir() and str(WINGET_LINKS) not in os.environ["PATH"]:
        os.environ["PATH"] = os.environ["PATH"] + os.pathsep + str(WINGET_LINKS)
    wwise = find_wwise()
    if wwise is None:
        raise PipelineError("No se encontró Wwise 2019–2023. Instálalo desde el Wwise Launcher (paquete Authoring).")

    audio_in = Path(opts.audio).resolve()
    tab = Path(opts.tab).resolve()
    if not audio_in.is_file():
        raise PipelineError(f"No existe el audio: {audio_in}")
    if not tab.is_file():
        raise PipelineError(f"No existe la tab: {tab}")
    log_lines = lambda text: [on_log(line) for line in text.strip().splitlines()]

    # Metadatos y pistas: lo que no venga en las opciones sale de la tab
    info = inspect_tab(tab)
    meta = {"artist": opts.artist or info["artist"] or "Desconocido",
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
        tracks_desc = f"Lead = pista {lead}" + (f", Rhythm = pista {rhythm}" if rhythm is not None else "")
    out = ROOT / "build" / (opts.name or slug(f"{meta['artist']} {meta['title']}"))
    out.mkdir(parents=True, exist_ok=True)
    on_step(0, f"{meta['artist']} - {meta['title']} | {tracks_desc}")

    if opts.lookup and (meta["year"] is None or meta["cover"] is None or not meta["album"]):
        found = lookup_song(meta["artist"], meta["title"], on_log)
        meta["album"] = meta["album"] or found.get("album") or ""
        meta["year"] = meta["year"] or found.get("year")
        if meta["cover"] is None and found.get("cover_url"):
            cover = download_cover(found["cover_url"], out / "cover.jpg", on_log)
            meta["cover"] = str(cover) if cover else None
        if found:
            on_log(f"iTunes: {meta['album']} ({meta['year']}), portada {'sí' if meta['cover'] else 'no'}")

    on_step(1, f"Audio → WAV (+{opts.pad:g} s de silencio)")
    try:
        wav = audio.to_wav(audio_in, out / "song.wav", pad_seconds=opts.pad)
    except subprocess.CalledProcessError as e:
        raise PipelineError("ffmpeg no pudo convertir el audio (¿archivo dañado o formato no soportado?)",
                            (e.stderr or b"").decode("utf-8", "replace")) from e
    except RuntimeError as e:                       # audio.ffmpeg_exe: no está en el PATH
        raise PipelineError("No se encontró ffmpeg. Instálalo (winget install Gyan.FFmpeg) y vuelve a intentarlo.") from e

    on_step(2, "Línea de tiempo de la tab")
    log_lines(rsbuild("timeline", str(tab), str(out / "timeline.json")))

    on_step(3, "Alineación tab ↔ audio (armonía + batería + tempo)")
    r = align.align_hybrid(out / "timeline.json", wav, out / "bar_times.json")
    bpm_note = (f"compás 1 en {r['bar_times_ms'][0] / 1000:.2f} s, relación de tempo {r['tempo_ratio_median']}, "
                f"corrección media sobre la armonía: {r['mean_abs_offset_ms']} ms, "
                f"compases con tempo raro: {len(r['suspicious_bars'])}")
    on_log(bpm_note)
    tl = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    if not any(n[2] < 0 for n in tl["notes"]):
        on_log("AVISO: la tab no tiene batería; la alineación fina será menos precisa.")

    lead_program = next((t["program"] for t in info["tracks"] if t["index"] == lead), None)
    tone_path, tone_desc = resolve_tone(opts.tone, meta["artist"], meta["title"], lead_program)
    on_step(4, f"XML, audio Wwise y empaquetado — tono: {tone_desc}")
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
    log = rsbuild(*args, env=dict(os.environ, WWISEROOT=wwise))
    log_lines(log)
    m = re.search(r"PSARC:\s+(.+)", log)
    if not m:
        raise PipelineError("RsBuild no informó del .psarc generado", log)
    psarc = Path(m.group(1).strip())

    report = out / "pkg" / "report.md"
    with report.open("a", encoding="utf-8") as f:
        f.write(f"\n## Pistas\n- {tracks_desc}\n")
        f.write(f"\n## Tono\n- {tone_desc}\n")
        f.write(f"\n## Alineación\n- {bpm_note}\n")
        if r["suspicious_bars"]:
            f.write(f"- Compases con tempo raro: {r['suspicious_bars'][:40]}\n")
        if r.get("gaps"):
            f.write(f"- Audio que no está en la tab (compás, segundos): {r['gaps']}\n")

    deployed = None
    if opts.deploy:
        deployed = deploy(psarc, on_log)
        on_step(5, f"Copiado a {deployed}")
        on_log("Vuelve a la pantalla de título del juego para que lo cargue.")
    else:
        on_step(5, f"Sin copiar a dlc/: {psarc}")
    return BuildResult(out_dir=out, psarc=psarc, report=report, deployed=deployed,
                       cover=Path(meta["cover"]) if meta["cover"] else None, meta=meta,
                       tracks=tracks_desc, tone=tone_desc, alignment=r)
