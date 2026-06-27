"""Manifest-driven clip segmentation and short-form video transforms."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Segment:
    """A planned subclip inside a source video."""

    index: int
    label: str
    start: float
    duration: float
    output_name: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SegmentManifest:
    """JSON-serializable segment plan for one input video."""

    source: str
    strategy: str
    segments: list[Segment]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["segments"] = [segment.to_dict() for segment in self.segments]
        return data


@dataclass
class MediaResult:
    input_path: str
    output_path: str
    start: Optional[float]
    duration: Optional[float]
    status: str
    encoding: str
    file_size_bytes: Optional[int]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _resolve_ffmpeg() -> str:
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


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout


def _clean_float(value: float) -> float:
    return int(value) if float(value).is_integer() else round(value, 3)


def plan_heuristic_segments(
    source: Path,
    clip_duration: float = 12.0,
    count: int = 6,
    start_offset: float = 4.0,
    gap: float = 1.0,
    label_prefix: str = "segment",
    output_prefix: Optional[str] = None,
) -> SegmentManifest:
    """Build fixed-window segment candidates for a rough run clip.

    This intentionally stays simple. The first version captures the repeatable
    process: create a manifest, review it, then refine by editing JSON or by
    rerunning with different timing values.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if clip_duration <= 0:
        raise ValueError("clip_duration must be positive")
    if start_offset < 0:
        raise ValueError("start_offset must be non-negative")

    prefix = output_prefix or source.stem
    segments: list[Segment] = []
    stride = clip_duration + gap
    for idx in range(1, count + 1):
        start = start_offset + ((idx - 1) * stride)
        label = f"{label_prefix}-{idx:02d}"
        segments.append(
            Segment(
                index=idx,
                label=label,
                start=_clean_float(start),
                duration=_clean_float(clip_duration),
                output_name=f"{prefix}-{idx:02d}-{label}.mp4",
            )
        )
    return SegmentManifest(
        source=str(source),
        strategy="heuristic-fixed-window",
        segments=segments,
    )


def load_manifest(path: Path) -> SegmentManifest:
    data = json.loads(path.read_text())
    segments = [
        Segment(
            index=int(item["index"]),
            label=str(item["label"]),
            start=float(item["start"]),
            duration=float(item["duration"]),
            output_name=str(item["output_name"]),
        )
        for item in data["segments"]
    ]
    return SegmentManifest(
        source=str(data["source"]),
        strategy=str(data.get("strategy", "unknown")),
        segments=segments,
    )


def write_manifest(manifest: SegmentManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")


def cut_segment(
    source: Path,
    output_path: Path,
    start: float,
    duration: float,
    force: bool = False,
    dry_run: bool = False,
) -> MediaResult:
    if output_path.exists() and not force and not dry_run:
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="exists",
            encoding="skipped",
            file_size_bytes=output_path.stat().st_size,
        )
    if dry_run:
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="dry-run",
            encoding="dry-run",
            file_size_bytes=None,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _resolve_ffmpeg()
    code, output = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    if code != 0 or not output_path.exists():
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="error",
            encoding="reencode",
            file_size_bytes=None,
            error=output.strip().splitlines()[-1] if output.strip() else "ffmpeg failed",
        )

    return MediaResult(
        input_path=str(source),
        output_path=str(output_path),
        start=start,
        duration=duration,
        status="created",
        encoding="reencode",
        file_size_bytes=output_path.stat().st_size,
    )


def cut_manifest(
    manifest: SegmentManifest,
    output_dir: Path,
    force: bool = False,
    dry_run: bool = False,
) -> list[MediaResult]:
    source = Path(manifest.source)
    results: list[MediaResult] = []
    for segment in manifest.segments:
        results.append(
            cut_segment(
                source=source,
                output_path=output_dir / segment.output_name,
                start=segment.start,
                duration=segment.duration,
                force=force,
                dry_run=dry_run,
            )
        )
    return results


def make_review_sheet(
    input_path: Path,
    output_path: Path,
    every_seconds: float = 3.0,
    columns: int = 5,
    width: int = 240,
    force: bool = False,
    dry_run: bool = False,
) -> MediaResult:
    if output_path.exists() and not force and not dry_run:
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=None,
            duration=None,
            status="exists",
            encoding="skipped",
            file_size_bytes=output_path.stat().st_size,
        )
    if dry_run:
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=None,
            duration=None,
            status="dry-run",
            encoding="dry-run",
            file_size_bytes=None,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _resolve_ffmpeg()
    fps = f"1/{every_seconds:g}"
    vf = f"fps={fps},scale={width}:-1,tile={columns}x1"
    code, output = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ]
    )
    if code != 0 or not output_path.exists():
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=None,
            duration=None,
            status="error",
            encoding="review-sheet",
            file_size_bytes=None,
            error=output.strip().splitlines()[-1] if output.strip() else "ffmpeg failed",
        )
    return MediaResult(
        input_path=str(input_path),
        output_path=str(output_path),
        start=None,
        duration=None,
        status="created",
        encoding="review-sheet",
        file_size_bytes=output_path.stat().st_size,
    )


def make_segment_review_sheet(
    source: Path,
    output_path: Path,
    start: float,
    duration: float,
    every_seconds: float = 3.0,
    columns: int = 5,
    width: int = 240,
    force: bool = False,
    dry_run: bool = False,
) -> MediaResult:
    if output_path.exists() and not force and not dry_run:
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="exists",
            encoding="skipped",
            file_size_bytes=output_path.stat().st_size,
        )
    if dry_run:
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="dry-run",
            encoding="dry-run",
            file_size_bytes=None,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _resolve_ffmpeg()
    fps = f"1/{every_seconds:g}"
    vf = f"fps={fps},scale={width}:-1,tile={columns}x1"
    code, output = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(duration),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ]
    )
    if code != 0 or not output_path.exists():
        return MediaResult(
            input_path=str(source),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="error",
            encoding="segment-review-sheet",
            file_size_bytes=None,
            error=output.strip().splitlines()[-1] if output.strip() else "ffmpeg failed",
        )
    return MediaResult(
        input_path=str(source),
        output_path=str(output_path),
        start=start,
        duration=duration,
        status="created",
        encoding="segment-review-sheet",
        file_size_bytes=output_path.stat().st_size,
    )


def make_manifest_review_sheets(
    manifest: SegmentManifest,
    output_dir: Path,
    every_seconds: float = 3.0,
    columns: int = 5,
    width: int = 240,
    force: bool = False,
    dry_run: bool = False,
) -> list[MediaResult]:
    source = Path(manifest.source)
    results: list[MediaResult] = []
    for segment in manifest.segments:
        output_name = Path(segment.output_name).with_suffix(".jpg").name
        results.append(
            make_segment_review_sheet(
                source=source,
                output_path=output_dir / output_name,
                start=segment.start,
                duration=segment.duration,
                every_seconds=every_seconds,
                columns=columns,
                width=width,
                force=force,
                dry_run=dry_run,
            )
        )
    return results


def vertical_center_crop(
    input_path: Path,
    output_path: Path,
    start: float = 0.0,
    duration: Optional[float] = None,
    height: int = 1920,
    width: int = 1080,
    force: bool = False,
    dry_run: bool = False,
) -> MediaResult:
    if output_path.exists() and not force and not dry_run:
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="exists",
            encoding="skipped",
            file_size_bytes=output_path.stat().st_size,
        )
    if dry_run:
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="dry-run",
            encoding="dry-run",
            file_size_bytes=None,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _resolve_ffmpeg()
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-ss",
        str(start),
    ]
    if duration is not None:
        args.extend(["-t", str(duration)])
    args.extend(
        [
            "-vf",
            f"scale=-2:{height},crop={width}:{height}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    code, output = _run(args)
    if code != 0 or not output_path.exists():
        return MediaResult(
            input_path=str(input_path),
            output_path=str(output_path),
            start=start,
            duration=duration,
            status="error",
            encoding="vertical-center",
            file_size_bytes=None,
            error=output.strip().splitlines()[-1] if output.strip() else "ffmpeg failed",
        )
    return MediaResult(
        input_path=str(input_path),
        output_path=str(output_path),
        start=start,
        duration=duration,
        status="created",
        encoding="vertical-center",
        file_size_bytes=output_path.stat().st_size,
    )
