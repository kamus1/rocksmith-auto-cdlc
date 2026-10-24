"""CLI del generador de CDLC: audio + tab de Guitar Pro → .psarc de Rocksmith 2014.

Uso:
  python -m cdlc build --audio cancion.mp3 --tab cancion.gp
                       [--lead N] [--rhythm M | --no-rhythm] [--artist A --title T --album A --year Y]
                       [--tone categoría|archivo] [--cover img] [--name carpeta] [--no-deploy] [--no-lookup]
  python -m cdlc tones
  python -m cdlc inspect --tab cancion.gp
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import align, audio

ROOT = Path(__file__).resolve().parent.parent
RSBUILD = ROOT / "src" / "RsBuild" / "bin" / "Release" / "net10.0" / "RsBuild.dll"
DOTNET = shutil.which("dotnet") or r"C:\Program Files\dotnet\dotnet.exe"
DLC_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\dlc")
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
SUPPORTED_WWISE = re.compile(r"20(19|21|22|23)")


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
        sys.stderr.write(p.stdout + p.stderr)
        raise SystemExit(f"RsBuild {args[0]} falló (código {p.returncode})")
    return p.stdout


TONES_DIR = ROOT / "build" / "tones"
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
            raise SystemExit("No hay biblioteca de tonos: ejecuta primero `python -m cdlc tones`.")
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
            raise SystemExit(f"--tone debe ser un archivo o una categoría: {', '.join(TONE_CATEGORIES)}")
        name_hint = CATEGORY_NAME_HINT[tone.lower()]
        contradicts = re.compile(r"clean|acoust|acu|limpi" if tone.lower() not in ("clean", "acoustic")
                                 else r"dist|drive|od|fuzz|gain|lead", re.I)
        cands = [e for e in entries if e["Descriptors"] in wanted and not contradicts.search(e["Name"])]
        chosen = sorted(cands, key=lambda e: (not name_hint.search(e["Name"]), e["Descriptors"] != wanted[0],
                                              e["Arrangement"] != "Lead", e["File"]))[0] if cands else None
        if chosen is None:
            raise SystemExit(f"No hay tonos de la categoría {tone} en la biblioteca")
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


def choose_tracks(info: dict) -> tuple[int, int | None, str]:
    """Elige las pistas Lead y Rhythm de la tab. Devuelve (lead, rhythm, explicación)."""
    bars = max(1, info["bars"])
    guitars = [t for t in info["tracks"]
               if t["strings"] in (6, 7) and not t["is_percussion"] and t["bars_with_notes"] >= 0.1 * bars]
    if not guitars:
        # Sin guitarras: usar la pista con altura que más compases cubra (p. ej. un bajo tocado en guitarra)
        pitched = [t for t in info["tracks"] if not t["is_percussion"] and t["bars_with_notes"] > 0]
        if not pitched:
            raise SystemExit("La tab no tiene pistas con notas.")
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


def lookup_song(artist: str, title: str) -> dict:
    """Busca álbum, año y portada en la API pública de iTunes. Devuelve {} si no hay resultado."""
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    try:
        with urllib.request.urlopen(f"https://itunes.apple.com/search?{query}", timeout=10) as resp:
            results = json.loads(resp.read().decode("utf-8")).get("results", [])
    except OSError as e:
        print(f"      (sin conexión con iTunes: {e})")
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


def download_cover(url: str, dest: Path) -> Path | None:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            dest.write_bytes(resp.read())
        return dest
    except OSError as e:
        print(f"      (no se pudo descargar la portada: {e})")
        return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "cancion"


def cmd_build(a: argparse.Namespace) -> None:
    if WINGET_LINKS.is_dir():
        os.environ["PATH"] = os.environ["PATH"] + os.pathsep + str(WINGET_LINKS)
    wwise = find_wwise()
    if wwise is None:
        raise SystemExit("No se encontró Wwise 2019–2023. Instálalo desde el Wwise Launcher (paquete Authoring).")

    audio_in = Path(a.audio).resolve()
    tab = Path(a.tab).resolve()

    # Metadatos y pistas: lo que no venga por argumentos sale de la tab
    info = json.loads(rsbuild("inspect-json", str(tab)))
    meta = {"artist": a.artist or info["artist"] or "Desconocido",
            "title": a.title or info["title"] or Path(a.tab).stem,
            "album": a.album or info["album"] or "",
            "year": a.year, "cover": a.cover}
    if a.lead is None:
        lead, auto_rhythm, tracks_desc = choose_tracks(info)
        rhythm = a.rhythm if a.rhythm is not None else (None if a.no_rhythm else auto_rhythm)
        if a.no_rhythm and auto_rhythm is not None:
            tracks_desc = tracks_desc.split(", Rhythm")[0]
    else:
        lead, rhythm = a.lead, a.rhythm
        tracks_desc = f"Lead = pista {lead}" + (f", Rhythm = pista {rhythm}" if rhythm is not None else "")
    out = ROOT / "build" / (a.name or slug(f"{meta['artist']} {meta['title']}"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"[0/6] {meta['artist']} - {meta['title']} | {tracks_desc}")

    if not a.no_lookup and (meta["year"] is None or meta["cover"] is None or not meta["album"]):
        found = lookup_song(meta["artist"], meta["title"])
        meta["album"] = meta["album"] or found.get("album") or ""
        meta["year"] = meta["year"] or found.get("year")
        if meta["cover"] is None and found.get("cover_url"):
            cover = download_cover(found["cover_url"], out / "cover.jpg")
            meta["cover"] = str(cover) if cover else None
        if found:
            print(f"      iTunes: {meta['album']} ({meta['year']}), portada {'sí' if meta['cover'] else 'no'}")

    print(f"[1/6] Audio → WAV (+{a.pad:g} s de silencio)")
    wav = audio.to_wav(audio_in, out / "song.wav", pad_seconds=a.pad)

    print("[2/6] Detección de beats (beat_this)")
    beats = audio.detect_beats(wav, out / "beats.json")
    print(f"      {len(beats['beats_ms'])} beats en {beats['device']}")

    print("[3/6] Línea de tiempo de la tab")
    print("      " + rsbuild("timeline", str(tab), str(out / "timeline.json")).strip())

    print("[4/6] Alineación tab ↔ audio")
    align.align_beats_dp(out / "timeline.json", wav, out / "beats.json", out / "bar_times_grid.json")
    r = align.refine_local(out / "timeline.json", wav, out / "bar_times_grid.json", out / "bar_times.json")
    bpm_note = (f"compás 1 en {r['bar_times_ms'][0] / 1000:.2f} s, beats irregulares: {r['irregular_beat_intervals']}, "
                f"saltos de medio beat: {r['half_beat_jumps']}, cambios de relación beat/negra: {len(r['ratio_changes'])}, "
                f"ajuste fino medio: {r['refine_mean_abs_shift_ms']} ms")
    print(f"      {bpm_note}")
    # La batería da la medida más fiable (ataques nítidos); sin batería, la pista Lead
    tl = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    drums = sorted({int(n[3]) for n in tl["notes"] if n[2] < 0})
    ref_track, ref_name = (drums[0], "batería") if drums else (lead, f"pista {lead}")
    err = align.onset_error(out / "timeline.json", out / "bar_times.json", wav, ref_track)
    print(f"      error mediano de ataques ({ref_name}): {err['median_ms']} ms, "
          f"{err['within_50ms'] * 100:.0f}% a menos de 50 ms")

    lead_program = next((t["program"] for t in info["tracks"] if t["index"] == lead), None)
    tone_path, tone_desc = resolve_tone(a.tone, meta["artist"], meta["title"], lead_program)
    print(f"[5/6] XML, audio Wwise y empaquetado — tono: {tone_desc}")
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
    env = dict(os.environ, WWISEROOT=wwise)
    log = rsbuild(*args, env=env)
    print("      " + log.strip().replace("\n", "\n      "))
    psarc = Path(re.search(r"PSARC:\s+(.+)", log).group(1).strip())

    report = out / "pkg" / "report.md"
    with report.open("a", encoding="utf-8") as f:
        f.write(f"\n## Pistas\n- {tracks_desc}\n")
        f.write(f"\n## Tono\n- {tone_desc}\n")
        f.write(f"\n## Alineación\n- {bpm_note}\n- Error mediano de ataques ({ref_name}): {err['median_ms']} ms "
                f"(p90 {err['p90_ms']} ms, {err['within_50ms'] * 100:.0f}% < 50 ms)\n")
        if r["suspicious_bars"]:
            f.write(f"- Compases con tempo raro: {r['suspicious_bars'][:40]}\n")

    if a.no_deploy:
        print(f"[6/6] Sin copiar a dlc/: {psarc}")
    else:
        target = deploy(psarc)
        print(f"[6/6] Copiado a {target}")
        print("      Vuelve a la pantalla de título del juego para que lo cargue.")


def deploy(psarc: Path) -> Path:
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
            print(f"      No se pudo borrar {p.name} ({e}); bórralo con el juego cerrado.")
    return target


def cmd_tones(a: argparse.Namespace) -> None:
    print(rsbuild("tones", str(Path(a.dlc).resolve()), str(TONES_DIR)).strip())


def cmd_inspect(a: argparse.Namespace) -> None:
    print(rsbuild("inspect", str(Path(a.tab).resolve())))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cdlc", description="Audio + tab Guitar Pro → CDLC de Rocksmith 2014")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="genera el .psarc y lo copia a dlc/")
    b.add_argument("--audio", required=True)
    b.add_argument("--tab", required=True)
    b.add_argument("--lead", type=int, help="pista de la tab para el arrangement Lead (por defecto: automática)")
    b.add_argument("--rhythm", type=int, help="pista para el arrangement Rhythm (por defecto: automática)")
    b.add_argument("--no-rhythm", action="store_true", help="no generar arrangement Rhythm")
    b.add_argument("--artist")
    b.add_argument("--title")
    b.add_argument("--album")
    b.add_argument("--year", type=int)
    b.add_argument("--cover", help="portada (png/jpg)")
    b.add_argument("--tone", help=f"archivo de tono o categoría ({', '.join(TONE_CATEGORIES)}); "
                                  "por defecto: misma canción o mismo artista de la biblioteca")
    b.add_argument("--name", help="nombre de la carpeta en build/")
    b.add_argument("--pad", type=float, default=4.0, help="segundos de silencio al inicio (count-in)")
    b.add_argument("--no-deploy", action="store_true", help="no copiar a la carpeta dlc del juego")
    b.add_argument("--no-lookup", action="store_true", help="no buscar álbum, año y portada en iTunes")
    b.set_defaults(func=cmd_build)

    t = sub.add_parser("tones", help="extrae la biblioteca de tonos de los CDLC instalados")
    t.add_argument("--dlc", default=str(DLC_DIR))
    t.set_defaults(func=cmd_tones)

    i = sub.add_parser("inspect", help="muestra pistas, afinaciones y compases de la tab")
    i.add_argument("--tab", required=True)
    i.set_defaults(func=cmd_inspect)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
