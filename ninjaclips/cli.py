"""ninjaclips CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from .clip import rough_cut
from .download import DownloadConfig, download_urls
from .source_resolver import find_source_file, slugify, title_fragment
from .video_tools import (
    cut_manifest as cut_manifest_file,
    load_manifest,
    make_review_sheet,
    plan_heuristic_segments,
    vertical_center_crop,
    write_manifest,
)
from .wnl_bridge import check_index_status, find_appearances, resolve_db_path

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Ninja warrior content vault — download videos and produce clips.",
)


def _read_url_file(path: Path) -> list[str]:
    urls: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


@app.command()
def download(
    urls: Optional[list[str]] = typer.Argument(
        None,
        help="YouTube URLs (videos or playlists). Pass via args, --file, or stdin.",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        readable=True,
        help="Read URLs from a file (one per line; '#' comments allowed).",
    ),
    output_dir: Path = typer.Option(
        Path("./downloads"),
        "--output-dir",
        "-o",
        help="Where to write video files, sidecars, and the archive.",
    ),
    max_height: int = typer.Option(
        1080,
        "--max-height",
        help="Cap video resolution by height.",
    ),
    subs: bool = typer.Option(
        True,
        "--subs/--no-subs",
        help="Download subtitles + auto-captions for the transcript pipeline.",
    ),
    info_json: bool = typer.Option(
        True,
        "--info-json/--no-info-json",
        help="Write the .info.json metadata sidecar.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve metadata only; do not download media.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON record per URL on stdout (human messages go to stderr).",
    ),
) -> None:
    """Download YouTube videos for the ninja clips vault."""
    collected: list[str] = list(urls or [])
    if file is not None:
        collected.extend(_read_url_file(file))
    if not collected and not sys.stdin.isatty():
        collected.extend(
            line.strip()
            for line in sys.stdin
            if line.strip() and not line.strip().startswith("#")
        )

    if not collected:
        typer.echo(
            "No URLs provided. Pass URLs as args, --file PATH, or pipe via stdin.",
            err=True,
        )
        raise typer.Exit(code=2)

    config = DownloadConfig(
        output_dir=output_dir,
        max_height=max_height,
        subs=subs,
        info_json=info_json,
        dry_run=dry_run,
        json_output=json_output,
    )

    failures = download_urls(collected, config)
    raise typer.Exit(code=1 if failures else 0)


@app.command("index-status")
def index_status_command(
    athlete: str = typer.Option(..., "--athlete", "-a", help="Athlete name (fuzzy match against WNL)."),
    video: str = typer.Option(..., "--video", help="YouTube ID to check."),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db-path",
        help="Path to WNL SQLite DB (default $WNL_DB_PATH or ~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON readiness record."),
) -> None:
    """Check whether WNL has the athlete timestamp needed for reliable clipping."""
    try:
        status = check_index_status(athlete_query=athlete, youtube_id=video, db_path=db_path)
    except FileNotFoundError as exc:
        resolved = resolve_db_path(db_path)
        msg = (
            f"WNL DB not found at {resolved}. "
            "Set WNL_DB_PATH or run ninjaclips against a system that has "
            "WNL-Athlete-Video-Index installed."
        )
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "code": 2}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=2) from exc

    if json_out:
        payload = status.to_dict()
        payload["code"] = 0 if status.ready else 1
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    elif status.ready:
        print(f"READY: {status.message}", file=sys.stderr)
        for appearance in status.appearances or []:
            timestamp = appearance["timestamp_seconds"]
            print(f"  - timestamp={timestamp}s", file=sys.stderr)
    else:
        print(f"WARNING: {status.message}", file=sys.stderr)
        print(
            "Proceeding without an indexed athlete timestamp is likely to produce "
            "bad rough cuts unless you manually confirm exact start/end times.",
            file=sys.stderr,
        )

    raise typer.Exit(code=0 if status.ready else 1)


@app.command()
def clip(
    athlete: str = typer.Option(..., "--athlete", "-a", help="Athlete name (fuzzy match against WNL)."),
    video: Optional[str] = typer.Option(None, "--video", help="Limit to a single YouTube ID."),
    downloads_dir: Path = typer.Option(
        Path("./downloads"),
        "--downloads-dir",
        help="Where source .mp4 files live.",
    ),
    output_dir: Path = typer.Option(
        Path("./clips"),
        "--output-dir",
        "-o",
        help="Where to write rough-cut .mp4 files.",
    ),
    pre_pad: int = typer.Option(5, "--pre-pad", help="Seconds before the timestamp to include."),
    duration: int = typer.Option(90, "--duration", help="Max clip duration in seconds."),
    reencode: bool = typer.Option(
        False,
        "--reencode",
        help="Re-encode instead of stream-copy (slower, no frozen leader).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing clip files."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve everything but skip ffmpeg invocation.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db-path",
        help="Path to WNL SQLite DB (default $WNL_DB_PATH or ~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON array on stdout (one record per appearance).",
    ),
) -> None:
    """Cut rough clips for an athlete from already-downloaded source videos."""
    try:
        appearances, matches = find_appearances(athlete, db_path=db_path)
    except FileNotFoundError as exc:
        resolved = resolve_db_path(db_path)
        msg = (
            f"WNL DB not found at {resolved}. "
            "Set WNL_DB_PATH or run ninjaclips against a system that has "
            "WNL-Athlete-Video-Index installed."
        )
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "code": 2}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=2) from exc

    if not matches:
        msg = f"No athlete in WNL matched '{athlete}'."
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "matches": [], "code": 1}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=1)

    if not appearances:
        # Ambiguous: matches has >1 entries above threshold and no clear winner.
        candidates = [
            {"display_name": m.display_name, "matched_on": m.matched_on, "score": m.score}
            for m in matches
        ]
        msg = f"Ambiguous athlete query: matched {len(matches)} athletes"
        if json_out:
            sys.stdout.write(
                json.dumps({"error": msg, "matches": candidates, "code": 1}) + "\n"
            )
        else:
            print(f"{msg}. Disambiguate by name or use --video:", file=sys.stderr)
            for c in candidates:
                print(
                    f"  - {c['display_name']} (matched on '{c['matched_on']}', score {c['score']:.0f})",
                    file=sys.stderr,
                )
        raise typer.Exit(code=1)

    if video:
        appearances = [a for a in appearances if a.youtube_id == video]
        if not appearances:
            msg = f"No appearances for athlete in video {video}."
            if json_out:
                sys.stdout.write(json.dumps({"error": msg, "code": 1}) + "\n")
            else:
                print(msg, file=sys.stderr)
            raise typer.Exit(code=1)

    canonical = appearances[0].athlete_name
    if not json_out:
        print(
            f"Resolved '{athlete}' → {canonical} ({len(appearances)} appearance(s))",
            file=sys.stderr,
        )

    # Group by video to enable per-video duration capping (next-appearance bound).
    by_video: dict[str, list] = {}
    for a in appearances:
        by_video.setdefault(a.youtube_id, []).append(a)
    for ids in by_video.values():
        ids.sort(key=lambda a: a.timestamp_seconds)

    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    had_error = False

    for yid, group in by_video.items():
        source = find_source_file(yid, downloads_dir)
        if source is None:
            record = {
                "status": "unavailable",
                "youtube_id": yid,
                "video_title": group[0].video_title,
                "athlete": canonical,
                "hint": f"ninjaclips download https://www.youtube.com/watch?v={yid}",
            }
            records.append(record)
            if not json_out:
                print(
                    f"[unavailable] {yid} not in {downloads_dir}/ — "
                    f"`ninjaclips download https://www.youtube.com/watch?v={yid}`",
                    file=sys.stderr,
                )
            continue

        fragment = title_fragment(source)
        slug = slugify(canonical)

        for idx, app_row in enumerate(group):
            start = max(0, app_row.timestamp_seconds - pre_pad)
            # Cap duration so we don't spill into the next athlete's window.
            effective = duration
            if idx + 1 < len(group):
                next_start = max(0, group[idx + 1].timestamp_seconds - pre_pad)
                gap = next_start - start
                if 0 < gap < effective:
                    effective = gap

            out_name = f"{slug} - {fragment} [{yid}] [{start:06d}].mp4"
            out_path = output_dir / out_name

            result = rough_cut(
                source_file=source,
                output_path=out_path,
                youtube_id=yid,
                athlete=canonical,
                start=start,
                duration=effective,
                reencode=reencode,
                dry_run=dry_run,
                force=force,
            )
            record = result.to_dict()
            record["video_title"] = group[0].video_title
            record["timestamp_seconds"] = app_row.timestamp_seconds
            record["pre_pad"] = pre_pad
            records.append(record)

            if result.status == "error":
                had_error = True

            if not json_out:
                tag = result.status.upper()
                size = (
                    f"{result.file_size_bytes / 1_000_000:.1f}MB"
                    if result.file_size_bytes
                    else "-"
                )
                print(
                    f"[{tag}] {out_path.name}  (start={start}s dur={effective}s "
                    f"enc={result.encoding} size={size})",
                    file=sys.stderr,
                )
                if result.error:
                    print(f"        error: {result.error}", file=sys.stderr)

    if json_out:
        sys.stdout.write(json.dumps(records, indent=2) + "\n")

    if had_error:
        raise typer.Exit(code=1)


@app.command("segment")
def segment_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Rough run clip to segment."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write a JSON segment manifest to this path. Defaults to stdout.",
    ),
    clip_duration: float = typer.Option(
        12.0,
        "--clip-duration",
        help="Duration for each heuristic segment in seconds.",
    ),
    count: int = typer.Option(6, "--count", help="Number of segments to plan."),
    start_offset: float = typer.Option(
        4.0,
        "--start-offset",
        help="Seconds into the rough clip where segmentation starts.",
    ),
    gap: float = typer.Option(
        1.0,
        "--gap",
        help="Seconds between heuristic segment windows.",
    ),
    label_prefix: str = typer.Option(
        "obstacle",
        "--label-prefix",
        help="Prefix for generated segment labels.",
    ),
    output_prefix: Optional[str] = typer.Option(
        None,
        "--output-prefix",
        help="Prefix for generated segment file names.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the manifest JSON on stdout even when --output is provided.",
    ),
) -> None:
    """Create a heuristic segment manifest for a rough run clip."""
    try:
        manifest = plan_heuristic_segments(
            source=source,
            clip_duration=clip_duration,
            count=count,
            start_offset=start_offset,
            gap=gap,
            label_prefix=label_prefix,
            output_prefix=output_prefix,
        )
    except ValueError as exc:
        msg = {"error": str(exc), "code": 1}
        sys.stdout.write(json.dumps(msg) + "\n")
        raise typer.Exit(code=1) from exc

    if output is not None:
        write_manifest(manifest, output)
        if not json_out:
            print(f"Wrote manifest: {output}", file=sys.stderr)

    if output is None or json_out:
        sys.stdout.write(json.dumps(manifest.to_dict(), indent=2) + "\n")


@app.command("cut-manifest")
def cut_manifest_command(
    manifest_path: Path = typer.Argument(..., exists=True, readable=True, help="Segment manifest JSON."),
    output_dir: Path = typer.Option(
        Path("./obstacle-clips"),
        "--output-dir",
        "-o",
        help="Where to write generated clips.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing output files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve outputs but skip ffmpeg."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON records on stdout."),
) -> None:
    """Cut all segments from a segment manifest."""
    manifest = load_manifest(manifest_path)
    results = cut_manifest_file(
        manifest=manifest,
        output_dir=output_dir,
        force=force,
        dry_run=dry_run,
    )
    if json_out:
        sys.stdout.write(json.dumps([r.to_dict() for r in results], indent=2) + "\n")
    else:
        for result in results:
            size = (
                f"{result.file_size_bytes / 1_000_000:.1f}MB"
                if result.file_size_bytes
                else "-"
            )
            print(
                f"[{result.status.upper()}] {result.output_path} "
                f"(start={result.start}s dur={result.duration}s size={size})",
                file=sys.stderr,
            )
            if result.error:
                print(f"        error: {result.error}", file=sys.stderr)

    if any(r.status == "error" for r in results):
        raise typer.Exit(code=1)


@app.command("review-sheet")
def review_sheet_command(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="Clip to summarize as frames."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output image path. Defaults to review-sheets/{input-stem}.jpg.",
    ),
    every_seconds: float = typer.Option(
        3.0,
        "--every-seconds",
        help="Frame sampling interval in seconds.",
    ),
    columns: int = typer.Option(5, "--columns", help="Number of frames in the contact strip."),
    width: int = typer.Option(240, "--width", help="Width of each sampled frame."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing output."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve output but skip ffmpeg."),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON record on stdout."),
) -> None:
    """Create a quick contact-strip image for visual review."""
    output_path = output or (Path("./review-sheets") / f"{input_path.stem}.jpg")
    result = make_review_sheet(
        input_path=input_path,
        output_path=output_path,
        every_seconds=every_seconds,
        columns=columns,
        width=width,
        force=force,
        dry_run=dry_run,
    )
    if json_out:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        print(f"[{result.status.upper()}] {result.output_path}", file=sys.stderr)
        if result.error:
            print(f"        error: {result.error}", file=sys.stderr)
    if result.status == "error":
        raise typer.Exit(code=1)


@app.command("vertical")
def vertical_command(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="Clip to export vertically."),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output mp4 path. Defaults to vertical-clips/{input-stem}-vertical.mp4.",
    ),
    start: float = typer.Option(0.0, "--start", help="Start time inside input clip."),
    duration: Optional[float] = typer.Option(
        None,
        "--duration",
        help="Optional duration in seconds.",
    ),
    crop: str = typer.Option(
        "center",
        "--crop",
        help="Crop strategy. Currently only 'center' is supported.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing output."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve output but skip ffmpeg."),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON record on stdout."),
) -> None:
    """Export a clip as 9:16 vertical video."""
    if crop != "center":
        msg = {"error": f"Unsupported crop strategy: {crop}", "supported": ["center"], "code": 1}
        sys.stdout.write(json.dumps(msg) + "\n")
        raise typer.Exit(code=1)

    output_path = output or (Path("./vertical-clips") / f"{input_path.stem}-vertical.mp4")
    result = vertical_center_crop(
        input_path=input_path,
        output_path=output_path,
        start=start,
        duration=duration,
        force=force,
        dry_run=dry_run,
    )
    if json_out:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        size = (
            f"{result.file_size_bytes / 1_000_000:.1f}MB"
            if result.file_size_bytes
            else "-"
        )
        print(f"[{result.status.upper()}] {result.output_path} (size={size})", file=sys.stderr)
        if result.error:
            print(f"        error: {result.error}", file=sys.stderr)
    if result.status == "error":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
