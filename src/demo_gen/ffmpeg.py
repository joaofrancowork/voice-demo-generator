"""Thin, testable wrappers around ffmpeg and ffprobe."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
FPS = 30


class FFmpegError(RuntimeError):
    pass


def run(args: list[str]) -> str:
    log.debug("run: %s", " ".join(args))
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"{args[0]} failed: {result.stderr.strip()[-800:]}")
    return result.stdout


def duration(path: Path) -> float:
    out = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
    )
    return float(json.loads(out)["format"]["duration"])


def scene_clip(
    visual: Path,
    audio: Path,
    out: Path,
    seconds: float,
    size: tuple[int, int],
    is_video: bool,
) -> list[str]:
    """Command for one scene: visual fitted to the frame (no stretching), narration, fixed length.

    Videos are looped if shorter than the narration and trimmed if longer; stills get a
    slow 4% push-in so the frame never looks frozen.
    """
    w, h = size
    fit = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}"
    )
    if is_video:
        inputs = ["-stream_loop", "-1", "-i", str(visual)]
        video_filter = fit
    else:
        frames = max(1, round(seconds * FPS))
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(visual)]
        video_filter = (
            f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=decrease,"
            f"pad={w * 2}:{h * 2}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"zoompan=z='1+0.04*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={w}x{h}:fps={FPS},setsar=1"
        )
    return [
        "ffmpeg", "-y", *inputs, "-i", str(audio),
        "-filter_complex", f"[0:v]{video_filter}[v];[1:a]apad[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out),
    ]  # fmt: skip


def concat(clips: list[Path], out: Path, workdir: Path) -> list[str]:
    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips))
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-c",
        "copy",
        str(out),
    ]


def finish(video: Path, captions: Path, out: Path) -> list[str]:
    """Overlay the caption track and normalise loudness to -14 LUFS (social platforms)."""
    return [
        "ffmpeg", "-y", "-i", str(video), "-i", str(captions),
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
        "-map", "[v]", "-map", "0:a",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
