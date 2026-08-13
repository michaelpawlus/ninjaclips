"""Shared ffmpeg/ffprobe resolution and source probing.

Every timestamp in this project is **source-absolute**: seconds measured from
the start of the original downloaded video, never from the start of some
intermediate clip. Probing the source is what makes that possible, so it lives
here next to the binary resolution.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


class FFmpegMissingError(RuntimeError):
    """Neither a PATH ffmpeg nor the static_ffmpeg fallback is available."""


def _resolve_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        pass
    found = shutil.which(name)
    if not found:
        raise FFmpegMissingError(
            f"{name} not found on PATH and static_ffmpeg is unavailable. "
            f"Install it with `brew install ffmpeg`."
        )
    return found


def resolve_ffmpeg() -> str:
    return _resolve_tool("ffmpeg")


def resolve_ffprobe() -> str:
    return _resolve_tool("ffprobe")


def run(args: list[str]) -> tuple[int, str]:
    """Run a media tool, returning (returncode, combined stdout+stderr)."""
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


@dataclass
class MediaInfo:
    """What we need to know about a source before cutting it."""

    path: str
    duration: float | None
    width: int | None
    height: int | None
    # The rate to encode against. See probe_video for why this is not simply
    # the measured rate.
    fps: float | None
    nominal_fps: float | None
    measured_fps: float | None
    is_vfr: bool
    vcodec: str | None
    acodec: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_rate(value: str | None) -> float | None:
    """Parse an ffprobe rational like '30000/1001' into a float."""
    if not value or value in ("0/0", "N/A"):
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            numerator, denominator = float(num), float(den)
        except ValueError:
            return None
        if denominator == 0:
            return None
        return numerator / denominator
    try:
        return float(value)
    except ValueError:
        return None


def probe_video(path: Path) -> MediaInfo:
    """Read stream metadata from a video file via ffprobe."""
    ffprobe = resolve_ffprobe()
    code, output = run(
        [
            ffprobe,
            "-hide_banner",
            "-loglevel", "error",
            "-show_streams",
            "-show_format",
            "-print_format", "json",
            str(path),
        ]
    )
    if code != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {output.strip()}")

    data = json.loads(output)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = None
    raw_duration = (data.get("format") or {}).get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None

    # r_frame_rate is the nominal/base rate; avg_frame_rate is measured across
    # the file. They diverge on variable-frame-rate sources — common from
    # YouTube — where frame-index math silently desyncs from wall-clock time.
    r_rate = _parse_rate(video.get("r_frame_rate")) if video else None
    avg_rate = _parse_rate(video.get("avg_frame_rate")) if video else None
    is_vfr = bool(
        r_rate and avg_rate and abs(r_rate - avg_rate) > 0.01 * max(r_rate, avg_rate)
    )

    # For a CFR source, r_frame_rate is the exact nominal rate (30, or 30000/1001)
    # while avg_frame_rate is derived from frame count over duration and lands
    # slightly off — encoding at a measured 29.999998 would make a non-standard
    # file for no reason. For a genuinely VFR source there is no meaningful
    # nominal rate, so the measured average is the honest target.
    fps = (avg_rate or r_rate) if is_vfr else (r_rate or avg_rate)

    return MediaInfo(
        path=str(path),
        duration=duration,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=fps,
        nominal_fps=r_rate,
        measured_fps=avg_rate,
        is_vfr=is_vfr,
        vcodec=video.get("codec_name") if video else None,
        acodec=audio.get("codec_name") if audio else None,
    )
