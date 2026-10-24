"""Preparación del audio para el pipeline de CDLC."""

import shutil
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("No se encontró ffmpeg en el PATH")
    return exe


def to_wav(src: Path, dst: Path, pad_seconds: float = 0.0) -> Path:
    """Convierte a WAV 48 kHz estéreo 16 bits, añadiendo pad_seconds de silencio al inicio."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = int(round(pad_seconds * 1000))
    afilter = f"adelay={delay_ms}:all=1" if delay_ms > 0 else "anull"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
         "-af", afilter, "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", str(dst)],
        check=True,
    )
    return dst


def duration_ms(wav: Path) -> int:
    import soundfile as sf

    info = sf.info(str(wav))
    return int(info.frames * 1000 / info.samplerate)
