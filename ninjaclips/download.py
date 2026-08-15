"""Download adapter around yt-dlp."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from .source_resolver import find_source_file


def _ensure_ffmpeg() -> None:
    """Make ffmpeg/ffprobe available on PATH so yt-dlp can merge formats."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except ImportError:
        # Fallback: yt-dlp will warn and skip merging.
        pass


@dataclass
class DownloadConfig:
    output_dir: Path
    max_height: int = 1080
    subs: bool = True
    info_json: bool = True
    dry_run: bool = False
    json_output: bool = False
    # Fetch only [section_start, section_end) instead of the whole video.
    section_start: float | None = None
    section_end: float | None = None

    @property
    def sectioned(self) -> bool:
        return self.section_start is not None and self.section_end is not None


def write_section_sidecar(path: Path, youtube_id: str, start: float, end: float) -> Path:
    """Record where a partial download sits inside the original video.

    A sectioned file's t=0 is *not* the source's t=0. Every timestamp in this
    project is source-absolute, so the offset has to be written down or every
    later cut against this file silently lands `start` seconds early.
    """
    sidecar = path.with_suffix(".section.json")
    sidecar.write_text(
        json.dumps(
            {
                "youtube_id": youtube_id,
                "section_start": start,
                "section_end": end,
                "note": (
                    "This file is a partial download. Add section_start to any "
                    "time measured within it to get a source-absolute timestamp."
                ),
            },
            indent=2,
        )
        + "\n"
    )
    return sidecar


def _ydl_opts(config: DownloadConfig) -> dict:
    out = config.output_dir
    # Don't constrain the codec: pinning [ext=mp4]/[ext=m4a] restricts YouTube
    # to AVC+AAC and hides the VP9/AV1+opus renditions, which are smaller at
    # equal quality and are the only source for >1080p.
    fmt = "bestvideo+bestaudio/best"
    opts: dict = {
        "format": fmt,
        # Cap via `res:N` rather than a `[height<=?N]` filter. yt-dlp's `res`
        # is the *smallest* dimension, so this caps the short side and is
        # correct in both orientations. A height filter is orientation-blind:
        # against a portrait 1080x1920 source, `height<=1440` excludes the
        # native rendition and silently selects 720x1280 — a real downgrade
        # that looks like a successful download.
        #
        # `fps` ahead of `vcodec` so a 60fps rendition wins over a
        # better-codec 30fps one — 60fps source halves the frame-interpolation
        # factor needed for a given slow-motion ramp.
        "format_sort": [f"res:{config.max_height}", "fps", "vcodec", "acodec", "br"],
        "merge_output_format": "mp4",
        "outtmpl": str(out / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
        "download_archive": str(out / "downloaded.txt"),
        "writesubtitles": config.subs,
        "writeautomaticsub": config.subs,
        "subtitleslangs": ["en", "en-US"],
        "subtitlesformat": "vtt",
        "writeinfojson": config.info_json,
        "ignoreerrors": True,
        "retries": 5,
        "fragment_retries": 5,
        "quiet": config.json_output,
        "no_warnings": config.json_output,
    }
    if config.sectioned:
        from yt_dlp.utils import download_range_func

        opts["download_ranges"] = download_range_func(
            None, [(config.section_start, config.section_end)]
        )
        # NOT force_keyframes_at_cuts. That option routes the ranged fetch
        # through ffmpeg, which requests the googlevideo URL without the
        # extracting client's identity and gets a hard 403 on every high-quality
        # format — the only ones that survive are the 360p pre-merged renditions.
        # yt-dlp's own fragment downloader has no such problem, still trims to
        # the exact requested window (measured: 15.001s for a 15s request, 450
        # frames at 30fps), and decodes cleanly from frame 0, so the partial-GOP
        # risk the flag guarded against does not materialise here.
        # Mark the range in the filename: a partial file must never be mistaken
        # for the full video, since cuts against it need an offset.
        opts["outtmpl"] = str(
            out
            / (
                "%(uploader)s - %(title)s [%(id)s] "
                f"[sec {int(config.section_start)}-{int(config.section_end)}].%(ext)s"
            )
        )
        # The archive records a bare video id, so a partial download would
        # otherwise make a later full download silently no-op.
        opts.pop("download_archive", None)

    if config.dry_run:
        opts["skip_download"] = True
    return opts


def _actual_filepath(entry: dict) -> str | None:
    """Return the path yt-dlp actually wrote, or None if nothing landed.

    `ydl.prepare_filename()` re-renders the output template and is only a
    prediction: for merged formats it reports the pre-merge extension, so it
    can name a file that does not exist. yt-dlp records what it really wrote
    in `requested_downloads[*].filepath`. Anything that deletes source media
    must use this, never the prediction.
    """
    for download in entry.get("requested_downloads") or []:
        path = download.get("filepath")
        if path:
            return path
    return None


def _emit_json(record: dict) -> None:
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def download_urls(urls: list[str], config: DownloadConfig) -> list[str]:
    """Download each URL. Returns list of URLs that failed."""
    _ensure_ffmpeg()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    if not config.json_output:
        action = "Resolving" if config.dry_run else "Downloading"
        print(
            f"{action} {len(urls)} URL(s) to {config.output_dir.resolve()}",
            file=sys.stderr,
        )
        print("-" * 60, file=sys.stderr)

    failures: list[str] = []

    with yt_dlp.YoutubeDL(_ydl_opts(config)) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=not config.dry_run)
            except Exception as exc:  # yt_dlp.utils.DownloadError and friends
                failures.append(url)
                if config.json_output:
                    _emit_json({"url": url, "status": "error", "error": str(exc)})
                else:
                    print(f"ERROR: {url}: {exc}", file=sys.stderr)
                continue

            if info is None:
                failures.append(url)
                if config.json_output:
                    _emit_json({"url": url, "status": "error", "error": "no info returned"})
                continue

            entries = info.get("entries") if info.get("_type") == "playlist" else [info]
            for entry in entries or []:
                if entry is None:
                    # `ignoreerrors` turns a failed playlist item into a None
                    # entry rather than raising.
                    failures.append(url)
                    if config.json_output:
                        _emit_json(
                            {"url": url, "status": "error", "error": "entry failed to extract"}
                        )
                    continue

                record = {
                    "url": entry.get("webpage_url") or url,
                    "id": entry.get("id"),
                    "title": entry.get("title"),
                    "uploader": entry.get("uploader"),
                    "duration": entry.get("duration"),
                    "upload_date": entry.get("upload_date"),
                }

                if config.dry_run:
                    record["status"] = "resolved"
                    record["filepath"] = None
                else:
                    # `ignoreerrors` lets a failed download still yield a fully
                    # populated entry, so presence of the entry proves nothing.
                    # Only a file on disk means the download succeeded.
                    path = _actual_filepath(entry)
                    if path and Path(path).exists():
                        record["status"] = "downloaded"
                        record["filepath"] = path
                        record["file_size_bytes"] = Path(path).stat().st_size
                        if config.sectioned:
                            record["section_start"] = config.section_start
                            record["section_end"] = config.section_end
                            record["section_sidecar"] = str(
                                write_section_sidecar(
                                    Path(path),
                                    entry.get("id") or "",
                                    config.section_start,
                                    config.section_end,
                                )
                            )
                    elif ydl.in_download_archive(entry):
                        # Already in downloaded.txt, so yt-dlp wrote nothing by
                        # design. Recover the existing file by globbing the id;
                        # this is a success, not a failure.
                        existing = find_source_file(entry.get("id") or "", config.output_dir)
                        record["status"] = "skipped"
                        record["filepath"] = str(existing) if existing else None
                        if existing:
                            record["file_size_bytes"] = existing.stat().st_size
                        else:
                            record["error"] = (
                                "in download archive but no matching file found "
                                f"in {config.output_dir} — media may have been pruned"
                            )
                    else:
                        record["status"] = "error"
                        record["filepath"] = None
                        record["error"] = (
                            "yt-dlp reported no written file "
                            "(download failed or was skipped by the archive)"
                        )
                        failures.append(entry.get("webpage_url") or url)

                if config.json_output:
                    _emit_json(record)
                elif record["status"] == "error":
                    print(f"ERROR: {record['url']}: {record['error']}", file=sys.stderr)

    return failures
