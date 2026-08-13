"""Rough-cut clipping via ffmpeg.

`rough_cut()` extracts an athlete's run from a source broadcast. The output is
the **durable artifact** for everything downstream — segmentation, analysis,
and rendering all read it, and the multi-gigabyte source is meant to be pruned
once the cut is confirmed. That makes two properties non-negotiable:

* **Frame-accurate.** `-ss` before `-i` gives fast input seek; combined with a
  re-encode, ffmpeg decodes from the preceding keyframe and discards frames
  before the seek point, so the cut lands on the requested frame. Stream-copy
  instead snaps to the nearest keyframe (seconds of drift on YouTube encodes),
  and that error would be inherited by every obstacle clip cut from this file.
* **Constant frame rate.** YouTube sources are frequently VFR, which silently
  desyncs frame-index math in downstream pose/audio analysis.

Stream-copy is still available via `fast_proxy=True` for a throwaway preview,
and is recorded as such in the ledger so nothing downstream mistakes it for an
accurate cut.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .ledger import ClipRecord, write_record
from .media import MediaInfo, probe_video, resolve_ffmpeg, run

# §8: H.264 high profile, crf 18-21, yuv420p for social compatibility.
DEFAULT_CRF = 20


@dataclass
class ClipResult:
    source_video_id: str
    athlete: str | None
    start: float
    duration: float
    output_path: str
    encoding: str  # "cfr-reencode" | "stream-copy-proxy" | "skipped" | "dry-run"
    file_size_bytes: int | None
    status: str    # "created" | "exists" | "dry-run" | "error"
    error: str | None = None
    frame_accurate: bool = True
    source_file: str | None = None
    ledger_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _accurate_cmd(
    ffmpeg: str,
    source: Path,
    start: float,
    duration: float,
    out: Path,
    fps: float | None,
    crf: int = DEFAULT_CRF,
) -> list[str]:
    """Frame-accurate, constant-frame-rate cut."""
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        # Input seek: fast (skips to the preceding keyframe) but, because we
        # re-encode, still frame-accurate at `start`.
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
    ]
    if fps:
        # Force CFR so frame index N == N/fps seconds in every later stage.
        args += ["-fps_mode", "cfr", "-r", f"{fps:.6f}"]
    args += [
        "-c:v", "libx264",
        "-profile:v", "high",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        # Keep the audio clock locked to the video clock; a cut that starts
        # mid-packet can otherwise introduce a small constant A/V offset that
        # would corrupt audio-event timestamps downstream.
        "-af", "aresample=async=1:first_pts=0",
        "-movflags", "+faststart",
        str(out),
    ]
    return args


def _proxy_cmd(
    ffmpeg: str, source: Path, start: float, duration: float, out: Path
) -> list[str]:
    """Stream-copy cut. Fast, but snaps to the nearest preceding keyframe."""
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", f"{start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(out),
    ]


def rough_cut(
    source_file: Path,
    output_path: Path,
    youtube_id: str,
    start: float,
    duration: float,
    athlete: str | None = None,
    origin: str = "wnl",
    label: str | None = None,
    wnl_timestamp: int | None = None,
    pre_pad: float | None = None,
    fast_proxy: bool = False,
    dry_run: bool = False,
    force: bool = False,
    info: MediaInfo | None = None,
    crf: int = DEFAULT_CRF,
    source_offset: float = 0.0,
) -> ClipResult:
    """Cut a rough clip from source_file at [start, start+duration).

    `start` is **source-absolute** — seconds from the beginning of the original
    video. `source_offset` is where `source_file` itself begins within that
    original (non-zero only for a partial `--sections` download), so the actual
    ffmpeg seek is `start - source_offset`. The ledger always records the
    source-absolute value, keeping timestamps comparable across full and
    partial downloads.

    Writes a ledger sidecar alongside the clip on success.
    """
    seek = start - source_offset
    if seek < 0:
        return ClipResult(
            source_video_id=youtube_id,
            athlete=athlete,
            start=start,
            duration=duration,
            output_path=str(output_path),
            encoding="none",
            file_size_bytes=None,
            status="error",
            error=(
                f"start {start:g}s is before this file begins "
                f"({source_offset:g}s into the source) — it was downloaded as a "
                "partial section that does not cover the requested window"
            ),
            source_file=str(source_file),
        )

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
            source_file=str(source_file),
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
            frame_accurate=not fast_proxy,
            source_file=str(source_file),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg()

    if info is None:
        info = probe_video(source_file)

    if fast_proxy:
        cmd = _proxy_cmd(ffmpeg, source_file, seek, duration, output_path)
        encoding = "stream-copy-proxy"
    else:
        cmd = _accurate_cmd(
            ffmpeg, source_file, seek, duration, output_path, info.fps, crf=crf
        )
        encoding = "cfr-reencode"

    code, output = run(cmd)

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
            frame_accurate=not fast_proxy,
            source_file=str(source_file),
        )

    record = ClipRecord(
        clip_path=str(output_path),
        source_file=str(source_file),
        youtube_id=youtube_id,
        source_start=start,
        duration=duration,
        origin=origin,
        athlete=athlete,
        label=label,
        wnl_timestamp=wnl_timestamp,
        pre_pad=pre_pad,
        source_fps=info.fps,
        source_width=info.width,
        source_height=info.height,
        source_was_vfr=info.is_vfr,
        output_fps=None if fast_proxy else info.fps,
        encoding=encoding,
        ffmpeg_cmd=cmd,
        source_offset=source_offset,
    )
    ledger_path = write_record(record)

    return ClipResult(
        source_video_id=youtube_id,
        athlete=athlete,
        start=start,
        duration=duration,
        output_path=str(output_path),
        encoding=encoding,
        file_size_bytes=output_path.stat().st_size,
        status="created",
        frame_accurate=not fast_proxy,
        source_file=str(source_file),
        ledger_path=str(ledger_path),
    )
