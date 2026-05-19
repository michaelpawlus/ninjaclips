"""Download adapter around yt-dlp."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yt_dlp


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


def _ydl_opts(config: DownloadConfig) -> dict:
    out = config.output_dir
    # Format fallback chain: prefer merged best video+audio mp4, but if ffmpeg
    # isn't installed yt-dlp will fail the merge — fall back to a pre-merged
    # single mp4, then to "best" of any container.
    fmt = (
        f"bestvideo[height<={config.max_height}][ext=mp4]+bestaudio[ext=m4a]"
        f"/best[height<={config.max_height}][ext=mp4]"
        f"/best[ext=mp4]/best"
    )
    opts: dict = {
        "format": fmt,
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
    if config.dry_run:
        opts["skip_download"] = True
    return opts


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
                    continue
                record = {
                    "url": entry.get("webpage_url") or url,
                    "id": entry.get("id"),
                    "title": entry.get("title"),
                    "uploader": entry.get("uploader"),
                    "duration": entry.get("duration"),
                    "upload_date": entry.get("upload_date"),
                    "status": "resolved" if config.dry_run else "downloaded",
                    "filepath": (
                        ydl.prepare_filename(entry)
                        if not config.dry_run
                        else None
                    ),
                }
                if config.json_output:
                    _emit_json(record)

    return failures
