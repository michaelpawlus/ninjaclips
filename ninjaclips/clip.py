"""Rough-cut clipping via ffmpeg.

`rough_cut()` extracts a window of video starting before an athlete's
appearance timestamp. Stream-copy by default for speed; re-encode on
request (or as a fallback when stream-copy fails).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ClipResult:
    source_video_id: str
    athlete: str
    start: int
    duration: int
    output_path: str
    encoding: str  # "copy" | "reencode" | "skipped" | "dry-run"
    file_size_bytes: Optional[int]
    status: str    # "created" | "exists" | "dry-run" | "error"
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_ffmpeg() -> str:
    """Return path to an ffmpeg binary, falling back to static_ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        pass
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError(
            "ffmpeg not found on PATH and static_ffmpeg not installed. "
            "Install ffmpeg or `pip install static-ffmpeg`."
        )
    return found


def _run_ffmpeg(args: list[str]) -> tuple[int, str]:
    """Run ffmpeg, returning (returncode, combined_output)."""
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout


def _stream_copy_cmd(ffmpeg: str, source: Path, start: int, duration: int, out: Path) -> list[str]:
    # `-ss` before `-i` is the fast (input-seek) form — it seeks the file
    # rather than decoding from 0. Combined with `-c copy` it produces a near
    # instant cut at the nearest preceding keyframe.
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(out),
    ]


def _reencode_cmd(ffmpeg: str, source: Path, start: int, duration: int, out: Path) -> list[str]:
    # `-ss` after `-i` decodes from 0 to the cut point so the output starts
    # exactly at `start` with no frozen leader. ~10× slower than copy.
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-ss", str(start),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]


def rough_cut(
    source_file: Path,
    output_path: Path,
    youtube_id: str,
    athlete: str,
    start: int,
    duration: int,
    reencode: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> ClipResult:
    """Cut a rough clip from source_file at [start, start+duration).

    Returns a ClipResult — caller decides how to emit it.
    """
    if output_path.exists() and not force and not dry_run:
        return ClipResult(
            source_video_id=youtube_id,
            athlete=athlete,
            start=start,
            duration=duration,
            output_path=str(output_path),
            encoding="skipped",
            file_size_bytes=output_path.stat().st_size,
            status="exists",
        )

    if dry_run:
        return ClipResult(
            source_video_id=youtube_id,
            athlete=athlete,
            start=start,
            duration=duration,
            output_path=str(output_path),
            encoding="dry-run",
            file_size_bytes=None,
            status="dry-run",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _resolve_ffmpeg()

    if reencode:
        code, output = _run_ffmpeg(_reencode_cmd(ffmpeg, source_file, start, duration, output_path))
        encoding = "reencode"
    else:
        code, output = _run_ffmpeg(_stream_copy_cmd(ffmpeg, source_file, start, duration, output_path))
        encoding = "copy"
        if code != 0:
            # Fall back to re-encode if stream copy fails (e.g. non-seekable
            # codec, fragmented mp4 without faststart, etc.).
            if output_path.exists():
                output_path.unlink()
            code, output = _run_ffmpeg(
                _reencode_cmd(ffmpeg, source_file, start, duration, output_path)
            )
            encoding = "reencode"

    if code != 0 or not output_path.exists():
        return ClipResult(
            source_video_id=youtube_id,
            athlete=athlete,
            start=start,
            duration=duration,
            output_path=str(output_path),
            encoding=encoding,
            file_size_bytes=None,
            status="error",
            error=output.strip().splitlines()[-1] if output.strip() else "ffmpeg failed",
        )

    return ClipResult(
        source_video_id=youtube_id,
        athlete=athlete,
        start=start,
        duration=duration,
        output_path=str(output_path),
        encoding=encoding,
        file_size_bytes=output_path.stat().st_size,
        status="created",
    )
