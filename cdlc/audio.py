"""Audio preparation for the CDLC pipeline."""

import shutil
import subprocess
from pathlib import Path


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("ffmpeg not found in PATH")
    return exe


def to_wav(src: Path, dst: Path, pad_seconds: float = 0.0) -> Path:
    """Convert to 48 kHz stereo 16-bit WAV, prepending pad_seconds of silence."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = int(round(pad_seconds * 1000))
    afilter = f"adelay={delay_ms}:all=1" if delay_ms > 0 else "anull"
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
         "-af", afilter, "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", str(dst)],
        check=True, capture_output=True,
    )
    return dst


def tags(src: Path) -> dict[str, str]:
    """Title and artist from the audio file's tags (empty if it has none or ffmpeg fails)."""
    try:
        err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(src)], capture_output=True).stderr
    except (OSError, RuntimeError):
        return {}
    out = {}
    for line in err.decode("utf-8", "replace").splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key.strip().lower() in ("title", "artist") and key.strip().lower() not in out:
            out[key.strip().lower()] = value.strip()
    return out


def duration_ms(wav: Path) -> int:
    import soundfile as sf

    info = sf.info(str(wav))
    return int(info.frames * 1000 / info.samplerate)
