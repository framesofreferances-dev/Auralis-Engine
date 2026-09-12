from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("FFmpeg is required and was not found on PATH")
    return executable


def run_ffmpeg(args: list[str]) -> None:
    ffmpeg = require_ffmpeg()
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(command, check=True)


def prepare_music(source: str | Path, output: str | Path, duration: float) -> Path:
    """Trim/loop a track to an exact duration and apply gentle fades."""
    if duration <= 0:
        raise ValueError("duration must be positive")

    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    run_ffmpeg([
        "-stream_loop", "-1",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-af", "afade=t=in:st=0:d=1.0,afade=t=out:st={:.3f}:d=1.0,loudnorm=I=-18:TP=-2:LRA=11".format(max(0.0, duration - 1.0)),
        "-vn",
        str(output),
    ])
    return output


def mix_narration(narration: str | Path, music: str | Path, output: str | Path) -> Path:
    """Mix narration over music with side-chain-style ducking.

    The music is reduced while narration is present. The narration remains the
    primary signal and is copied to the final stereo output.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Split the narration stream: one branch remains dry for the final mix,
    # while the other drives side-chain ducking on the background music.
    filter_graph = (
        "[0:a]aresample=48000,volume=1.0,asplit=2[narr_mix][narr_side];"
        "[1:a]aresample=48000,volume=0.18[music];"
        "[music][narr_side]sidechaincompress="
        "threshold=0.025:ratio=8:attack=20:release=350[ducked];"
        "[narr_mix][ducked]amix="
        "inputs=2:duration=first:dropout_transition=2[mix]"
    )

    run_ffmpeg([
        "-i", str(narration),
        "-i", str(music),
        "-filter_complex", filter_graph,
        "-map", "[mix]",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output),
    ])
    return output


def convert_audio(
    source: str | Path,
    output: str | Path | None = None,
    *,
    quality: str = "2",
) -> Path:
    """Convert an acquired audio asset to MP3 using FFmpeg."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Audio source does not exist: {source}")
    if not quality.isdigit() or not 0 <= int(quality) <= 9:
        raise ValueError("quality must be an FFmpeg libmp3lame VBR value from 0 to 9")

    if output is None:
        output = source.with_suffix(".mp3")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if source.resolve() == output.resolve():
        raise ValueError("source and output must be different files")

    run_ffmpeg([
        "-i", str(source),
        "-vn",
        "-codec:a", "libmp3lame",
        "-q:a", quality,
        str(output),
    ])
    return output


def audio_info(path: str | Path) -> dict[str, float | int | str | None]:
    """Return basic duration, container format, and size information."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio asset does not exist: {path}")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is required for audio inspection")

    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration,format_name,size",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    fmt = payload.get("format", {})
    duration_raw = fmt.get("duration")
    size_raw = fmt.get("size")
    return {
        "path": str(path),
        "duration": float(duration_raw) if duration_raw not in (None, "") else None,
        "format": str(fmt["format_name"]) if fmt.get("format_name") else None,
        "size_bytes": int(size_raw) if size_raw not in (None, "") else path.stat().st_size,
    }
