"""ninjaclips CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from .batch import BatchError, load_jobs
from .clip import rough_cut
from .download import DownloadConfig, download_urls
from .ledger import mark_confirmed, read_record
from .media import probe_video
from .prune import apply_prune, plan_prune
from .source_resolver import find_source_file, section_offset, slugify, title_fragment
from .timecode import TimecodeError, format_timecode, parse_timecode
from .video_tools import (
    cut_manifest as cut_manifest_file,
)
from .video_tools import (
    load_manifest,
    make_manifest_review_sheets,
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


DB_PATH_HELP = (
    "Path to the WNL SQLite DB (default: $WNL_DB_PATH, else "
    "~/projects/WNL-Athlete-Video-Index/data/wnl_athlete_video_index.db)."
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
    urls: list[str] | None = typer.Argument(
        None,
        help="YouTube URLs (videos or playlists). Pass via args, --file, or stdin.",
    ),
    file: Path | None = typer.Option(
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
    section: str | None = typer.Option(
        None,
        "--section",
        help=(
            "Download only a time range, e.g. '1:09:00-1:16:00' or '4140-4560'. "
            "Vastly faster and smaller than a full download, but the window can "
            "never be widened later — pad generously."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit one JSON record per URL on stdout (human messages go to stderr).",
    ),
) -> None:
    """Download YouTube videos for the ninja clips vault."""
    section_start = section_end = None
    if section is not None:
        try:
            raw_start, _, raw_end = section.partition("-")
            if not raw_end:
                raise TimecodeError("expected START-END")
            section_start = parse_timecode(raw_start)
            section_end = parse_timecode(raw_end)
        except TimecodeError as exc:
            typer.echo(f"Invalid --section {section!r}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        if section_end <= section_start:
            typer.echo(
                f"Invalid --section {section!r}: end must be after start.", err=True
            )
            raise typer.Exit(code=2)

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
        section_start=section_start,
        section_end=section_end,
    )

    failures = download_urls(collected, config)
    raise typer.Exit(code=1 if failures else 0)


@app.command("index-status")
def index_status_command(
    athlete: str = typer.Option(..., "--athlete", "-a", help="Athlete name (fuzzy match against WNL)."),
    video: str = typer.Option(..., "--video", help="YouTube ID to check."),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help=DB_PATH_HELP,
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


def _describe_clip(result, out_path: Path, start: float, dur: float) -> None:
    """Print a one-line human summary of a clip result to stderr."""
    size = (
        f"{result.file_size_bytes / 1_000_000:.1f}MB" if result.file_size_bytes else "-"
    )
    accuracy = "" if result.frame_accurate else "  !! keyframe-snapped, not frame-accurate"
    print(
        f"[{result.status.upper()}] {out_path.name}  "
        f"(start={start:g}s dur={dur:g}s enc={result.encoding} size={size}){accuracy}",
        file=sys.stderr,
    )
    if result.error:
        print(f"        error: {result.error}", file=sys.stderr)


def _manual_clip(
    at: float,
    end: float | None,
    duration: float,
    pre_pad: float,
    video: str | None,
    athlete: str | None,
    label: str | None,
    downloads_dir: Path,
    output_dir: Path,
    fast_proxy: bool,
    force: bool,
    dry_run: bool,
    json_out: bool,
) -> None:
    """Cut one clip at an operator-supplied timestamp, with no WNL lookup."""

    def fail(msg: str, code: int = 2):
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "code": code}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=code)

    if not video:
        fail("--at requires --video YOUTUBE_ID to identify the source file.")
    if end is not None and end <= at:
        fail(f"--end ({end:g}s) must be greater than --at ({at:g}s).")

    source = find_source_file(video, downloads_dir)
    if source is None:
        fail(
            f"{video} not found in {downloads_dir}/ — "
            f"run: ninjaclips download https://www.youtube.com/watch?v={video}",
            code=1,
        )

    start = max(0.0, at - pre_pad)
    # --end is the end of the run itself, so the clip must still cover the
    # pre-pad lead-in that `start` backed up into.
    effective = (end - start) if end is not None else duration

    offset = section_offset(source)
    info = probe_video(source)
    if info.duration is not None and (start - offset) >= info.duration:
        span = f"{format_timecode(offset)}-{format_timecode(offset + info.duration)}"
        fail(
            f"--at {at:g}s is past the end of the source file "
            f"(covers {span}, {info.duration:.1f}s long).",
            code=1,
        )

    name = slugify(athlete or label or "clip")
    out_path = output_dir / f"{name} - {title_fragment(source)} [{video}] [{int(start):06d}].mp4"

    if not json_out:
        vfr = " (source is VFR — output forced to CFR)" if info.is_vfr else ""
        print(
            f"Manual cut: {video} @ {at:g}s (clip {start:g}-{start + effective:g}s) "
            f"{info.width}x{info.height} @ {info.fps:.3f}fps{vfr}",
            file=sys.stderr,
        )

    result = rough_cut(
        source_file=source,
        output_path=out_path,
        youtube_id=video,
        athlete=athlete,
        label=label,
        start=start,
        duration=effective,
        origin="manual",
        pre_pad=pre_pad,
        fast_proxy=fast_proxy,
        dry_run=dry_run,
        force=force,
        info=info,
        source_offset=offset,
    )

    if json_out:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        _describe_clip(result, out_path, start, effective)

    raise typer.Exit(code=1 if result.status == "error" else 0)


@app.command()
def clip(
    athlete: str | None = typer.Option(
        None,
        "--athlete",
        "-a",
        help="Athlete name (fuzzy match against WNL). Optional when --at is given.",
    ),
    at: float | None = typer.Option(
        None,
        "--at",
        help=(
            "Manual mode: source-absolute timestamp in seconds where the run "
            "starts (the `t=` value from a YouTube URL). Skips WNL entirely; "
            "requires --video."
        ),
    ),
    end: float | None = typer.Option(
        None,
        "--end",
        help="Manual mode: source-absolute end timestamp. Overrides --duration.",
    ),
    label: str | None = typer.Option(
        None,
        "--label",
        help="Manual mode: name for the clip when no athlete is given.",
    ),
    video: str | None = typer.Option(None, "--video", help="Limit to a single YouTube ID."),
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
    pre_pad: float = typer.Option(5, "--pre-pad", help="Seconds before the timestamp to include."),
    duration: float = typer.Option(90, "--duration", help="Max clip duration in seconds."),
    fast_proxy: bool = typer.Option(
        False,
        "--fast-proxy",
        help=(
            "Stream-copy instead of re-encoding. Much faster, but snaps to the "
            "nearest keyframe (seconds of drift) and produces a variable-frame-rate "
            "file. Preview only — blocks `prune` and is unsafe for analysis."
        ),
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing clip files."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve everything but skip ffmpeg invocation.",
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help=DB_PATH_HELP,
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit a JSON array on stdout (one record per appearance).",
    ),
) -> None:
    """Cut rough clips for an athlete from already-downloaded source videos."""
    if at is not None:
        _manual_clip(
            at=at,
            end=end,
            duration=duration,
            pre_pad=pre_pad,
            video=video,
            athlete=athlete,
            label=label,
            downloads_dir=downloads_dir,
            output_dir=output_dir,
            fast_proxy=fast_proxy,
            force=force,
            dry_run=dry_run,
            json_out=json_out,
        )
        return

    if athlete is None:
        msg = "Provide --athlete for WNL lookup, or --at SECONDS --video ID for manual mode."
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "code": 2}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=2)

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
        # Probe once per source, not once per appearance.
        info = probe_video(source) if not dry_run else None
        offset = section_offset(source)

        for idx, app_row in enumerate(group):
            start = max(0.0, app_row.timestamp_seconds - pre_pad)
            # Cap duration so we don't spill into the next athlete's window.
            effective = duration
            if idx + 1 < len(group):
                next_start = max(0, group[idx + 1].timestamp_seconds - pre_pad)
                gap = next_start - start
                if 0 < gap < effective:
                    effective = gap

            out_name = f"{slug} - {fragment} [{yid}] [{int(start):06d}].mp4"
            out_path = output_dir / out_name

            result = rough_cut(
                source_file=source,
                output_path=out_path,
                youtube_id=yid,
                athlete=canonical,
                start=start,
                duration=effective,
                origin="wnl",
                wnl_timestamp=app_row.timestamp_seconds,
                pre_pad=pre_pad,
                fast_proxy=fast_proxy,
                dry_run=dry_run,
                force=force,
                info=info,
                source_offset=offset,
            )
            record = result.to_dict()
            record["video_title"] = group[0].video_title
            record["timestamp_seconds"] = app_row.timestamp_seconds
            record["pre_pad"] = pre_pad
            records.append(record)

            if result.status == "error":
                had_error = True

            if not json_out:
                _describe_clip(result, out_path, start, effective)

    if json_out:
        sys.stdout.write(json.dumps(records, indent=2) + "\n")

    if had_error:
        raise typer.Exit(code=1)


@app.command("batch")
def batch_command(
    jobs_file: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="CSV or JSON list of runs: url, start, end, athlete[, label].",
    ),
    downloads_dir: Path = typer.Option(Path("./downloads"), "--downloads-dir"),
    output_dir: Path = typer.Option(Path("./clips"), "--output-dir", "-o"),
    max_height: int = typer.Option(1440, "--max-height", help="Cap video resolution."),
    pre_pad: float = typer.Option(
        30, "--pre-pad", help="Seconds of lead-in kept before each start."
    ),
    post_pad: float = typer.Option(
        30, "--post-pad", help="Seconds kept after each end."
    ),
    sections: bool = typer.Option(
        False,
        "--sections",
        help=(
            "Download only each run's window instead of the whole video. Much "
            "faster and smaller, but the window can never be widened later."
        ),
    ),
    section_margin: float = typer.Option(
        120,
        "--section-margin",
        help="Extra seconds fetched either side of the padded window with --sections.",
    ),
    skip_download: bool = typer.Option(
        False, "--skip-download", help="Cut only; assume sources are already present."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing clips."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Resolve and report the plan without downloading."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON records on stdout."),
) -> None:
    """Download and rough-cut a list of runs, unattended.

    Every row is validated before the first download, so a typo in the last
    line fails immediately rather than an hour in. Clips are left UNCONFIRMED —
    review them with `confirm`, then reclaim disk with `prune`.
    """
    try:
        jobs = load_jobs(jobs_file)
    except BatchError as exc:
        if json_out:
            sys.stdout.write(json.dumps({"error": str(exc), "code": 2}) + "\n")
        else:
            print(f"Batch file error — nothing was downloaded.\n  {exc}", file=sys.stderr)
        raise typer.Exit(code=2) from exc

    if not jobs:
        print(f"No jobs found in {jobs_file}.", file=sys.stderr)
        raise typer.Exit(code=2)

    if not json_out:
        print(f"{len(jobs)} job(s) from {jobs_file}:", file=sys.stderr)
        for job in jobs:
            window = f"{format_timecode(job.start)}-{format_timecode(job.end)}"
            print(
                f"  [{job.row}] {job.youtube_id}  {window}  "
                f"({job.end - job.start:.0f}s)  {job.athlete or job.label}",
                file=sys.stderr,
            )
        print("-" * 60, file=sys.stderr)

    records: list[dict] = []
    failures = 0

    for job in jobs:
        clip_start = max(0.0, job.start - pre_pad)
        clip_end = job.end + post_pad
        record: dict = {
            "row": job.row,
            "youtube_id": job.youtube_id,
            "athlete": job.athlete,
            "label": job.label,
            "run_start": job.start,
            "run_end": job.end,
            "clip_start": clip_start,
            "clip_end": clip_end,
        }

        if not skip_download:
            config = DownloadConfig(
                output_dir=downloads_dir,
                max_height=max_height,
                dry_run=dry_run,
                json_output=False,
            )
            if sections:
                config.section_start = max(0.0, clip_start - section_margin)
                config.section_end = clip_end + section_margin
            if not json_out:
                scope = (
                    f"section {format_timecode(config.section_start)}-"
                    f"{format_timecode(config.section_end)}"
                    if sections
                    else "full video"
                )
                print(f"[{job.row}] downloading {job.youtube_id} ({scope})", file=sys.stderr)
            if download_urls([job.watch_url], config):
                record["status"] = "download-failed"
                records.append(record)
                failures += 1
                if not json_out:
                    print(f"[{job.row}] DOWNLOAD FAILED — skipping cut", file=sys.stderr)
                continue

        source = find_source_file(job.youtube_id, downloads_dir)
        if source is None:
            record["status"] = "source-missing"
            records.append(record)
            failures += 1
            if not json_out:
                print(f"[{job.row}] source not found in {downloads_dir}", file=sys.stderr)
            continue

        offset = section_offset(source)
        info = probe_video(source) if not dry_run else None
        name = slugify(job.athlete or job.label or "clip")
        out_path = output_dir / (
            f"{name} - {title_fragment(source)} [{job.youtube_id}] [{int(clip_start):06d}].mp4"
        )

        result = rough_cut(
            source_file=source,
            output_path=out_path,
            youtube_id=job.youtube_id,
            athlete=job.athlete,
            label=job.label,
            start=clip_start,
            duration=clip_end - clip_start,
            origin="batch",
            pre_pad=pre_pad,
            dry_run=dry_run,
            force=force,
            info=info,
            source_offset=offset,
        )
        record.update(result.to_dict())
        records.append(record)
        if result.status == "error":
            failures += 1
        if not json_out:
            _describe_clip(result, out_path, clip_start, clip_end - clip_start)

    if json_out:
        sys.stdout.write(json.dumps(records, indent=2) + "\n")
    else:
        done = len(jobs) - failures
        print(
            f"\n{done}/{len(jobs)} cut. All clips are UNCONFIRMED — review with "
            "`ninjaclips confirm <clip>`, then `ninjaclips prune` to reclaim disk.",
            file=sys.stderr,
        )

    raise typer.Exit(code=1 if failures else 0)


@app.command("confirm")
def confirm_command(
    clip_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Rough-cut clip to review and confirm."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Mark the clip confirmed. Without this, only builds the review sheet.",
    ),
    unconfirm: bool = typer.Option(
        False, "--unconfirm", help="Clear a previous confirmation."
    ),
    sheet_dir: Path = typer.Option(
        Path("./review-sheets"), "--sheet-dir", help="Where to write the contact sheet."
    ),
    every_seconds: float = typer.Option(3.0, "--every-seconds", help="Frame sampling interval."),
    columns: int = typer.Option(5, "--columns", help="Frames per row in the contact sheet."),
    rows: int = typer.Option(1, "--rows", help="Rows in the contact sheet grid."),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Skip contact-sheet generation."),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON record on stdout."),
) -> None:
    """Review a rough cut and mark it confirmed, unlocking `prune` for its source."""
    record = read_record(clip_path)
    if record is None:
        msg = (
            f"No ledger sidecar for {clip_path.name} "
            f"(expected {clip_path.with_suffix('.json').name}). "
            "Only clips produced by `ninjaclips clip` can be confirmed."
        )
        if json_out:
            sys.stdout.write(json.dumps({"error": msg, "code": 2}) + "\n")
        else:
            print(msg, file=sys.stderr)
        raise typer.Exit(code=2)

    sheet_path = None
    if not no_sheet:
        sheet = make_review_sheet(
            input_path=clip_path,
            output_path=sheet_dir / f"{clip_path.stem}.jpg",
            every_seconds=every_seconds,
            columns=columns,
            rows=rows,
            force=True,
        )
        sheet_path = sheet.output_path
        if not json_out:
            print(f"Review sheet: {sheet.output_path}", file=sys.stderr)

    if yes or unconfirm:
        record = mark_confirmed(clip_path, confirmed=not unconfirm)

    payload = record.to_dict()
    payload["review_sheet"] = sheet_path
    if json_out:
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        state = "CONFIRMED" if record.confirmed else "UNCONFIRMED"
        print(
            f"[{state}] {clip_path.name}\n"
            f"  source      {Path(record.source_file).name}\n"
            f"  source time {record.source_start:g}s → "
            f"{record.source_start + record.duration:g}s ({record.duration:g}s)\n"
            f"  encoding    {record.encoding}",
            file=sys.stderr,
        )
        if not record.confirmed:
            print(
                "  Inspect the clip, then re-run with --yes to confirm.",
                file=sys.stderr,
            )


@app.command("prune")
def prune_command(
    youtube_id: str | None = typer.Option(
        None, "--video", help="Limit to a single YouTube ID."
    ),
    clips_dir: Path = typer.Option(
        Path("./clips"), "--clips-dir", help="Where rough cuts and their sidecars live."
    ),
    downloads_dir: Path = typer.Option(
        Path("./downloads"), "--downloads-dir", help="Where source videos live."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Actually delete. Without this, only reports."
    ),
    delete_metadata: bool = typer.Option(
        False,
        "--delete-metadata",
        help="Also delete .info.json/.vtt sidecars (kept by default).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON records on stdout."),
) -> None:
    """Delete source videos whose derived clips are all confirmed."""
    candidates = plan_prune(
        clips_dir=clips_dir, downloads_dir=downloads_dir, youtube_id=youtube_id
    )

    if not candidates:
        if json_out:
            sys.stdout.write(json.dumps([]) + "\n")
        else:
            print("Nothing to consider — no clips and no source videos.", file=sys.stderr)
        return

    deletable = [c for c in candidates if c.deletable]
    reclaimed = sum(c.size_bytes or 0 for c in deletable)

    if yes and deletable:
        apply_prune(deletable, keep_metadata=not delete_metadata)

    if json_out:
        sys.stdout.write(json.dumps([c.to_dict() for c in candidates], indent=2) + "\n")
        return

    for candidate in candidates:
        size = f"{candidate.size_bytes / 1_000_000_000:.2f}GB" if candidate.size_bytes else "-"
        if candidate.deleted:
            tag = "DELETED"
        elif candidate.deletable:
            tag = "WOULD DELETE"
        else:
            tag = "KEEP"
        print(
            f"[{tag}] {Path(candidate.source_file).name}  ({size}) — {candidate.reason}",
            file=sys.stderr,
        )
        for blocker in candidate.blocking_clips:
            print(f"          blocked by: {blocker}", file=sys.stderr)

    if not deletable:
        print("\nNothing is eligible for deletion.", file=sys.stderr)
    elif yes:
        print(
            f"\nDeleted {len(deletable)} source file(s), "
            f"reclaiming {reclaimed / 1_000_000_000:.2f}GB.",
            file=sys.stderr,
        )
    else:
        print(
            f"\n{len(deletable)} source file(s) eligible, "
            f"{reclaimed / 1_000_000_000:.2f}GB reclaimable. "
            "Re-run with --yes to delete.",
            file=sys.stderr,
        )


@app.command("segment")
def segment_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Rough run clip to segment."),
    output: Path | None = typer.Option(
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
    output_prefix: str | None = typer.Option(
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
    output: Path | None = typer.Option(
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
    columns: int = typer.Option(5, "--columns", help="Frames per row in the contact sheet."),
    rows: int = typer.Option(1, "--rows", help="Rows in the contact sheet grid."),
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
        rows=rows,
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


@app.command("review-manifest")
def review_manifest_command(
    manifest_path: Path = typer.Argument(..., exists=True, readable=True, help="Segment manifest JSON."),
    output_dir: Path = typer.Option(
        Path("./review-sheets"),
        "--output-dir",
        "-o",
        help="Where to write generated contact sheets.",
    ),
    every_seconds: float = typer.Option(
        3.0,
        "--every-seconds",
        help="Frame sampling interval in seconds.",
    ),
    columns: int = typer.Option(5, "--columns", help="Frames per row in each contact sheet."),
    rows: int = typer.Option(1, "--rows", help="Rows in each contact sheet grid."),
    width: int = typer.Option(240, "--width", help="Width of each sampled frame."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing outputs."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve outputs but skip ffmpeg."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON records on stdout."),
) -> None:
    """Create contact-strip review sheets for every segment in a manifest."""
    manifest = load_manifest(manifest_path)
    results = make_manifest_review_sheets(
        manifest=manifest,
        output_dir=output_dir,
        every_seconds=every_seconds,
        columns=columns,
        rows=rows,
        width=width,
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


@app.command("vertical")
def vertical_command(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="Clip to export vertically."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output mp4 path. Defaults to vertical-clips/{input-stem}-vertical.mp4.",
    ),
    start: float = typer.Option(0.0, "--start", help="Start time inside input clip."),
    duration: float | None = typer.Option(
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
