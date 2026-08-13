"""Read a batch job list: one row per run to archive.

The operator supplies cut points because automatic boundary detection is not
reliable yet. This module only parses and validates that list — the CLI does
the downloading and cutting, so a bad row fails loudly before any multi-gigabyte
download starts.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .timecode import TimecodeError, parse_timecode

FIELDS = ("url", "start", "end", "athlete", "label")
_HEADER_HINTS = {"url", "video", "video_id", "youtube_id", "link"}


class BatchError(ValueError):
    """The batch file could not be parsed."""


@dataclass
class BatchJob:
    """One run to download and cut."""

    url: str
    start: float
    end: float
    athlete: str | None = None
    label: str | None = None
    row: int = 0

    @property
    def youtube_id(self) -> str:
        """Extract the 11-character video id from a URL, or return it as-is."""
        text = self.url.strip()
        for marker in ("v=", "/live/", "/shorts/", "youtu.be/", "/embed/"):
            if marker in text:
                text = text.split(marker, 1)[1]
                break
        else:
            return text
        for sep in ("&", "?", "/", "#"):
            text = text.split(sep, 1)[0]
        return text

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.youtube_id}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["youtube_id"] = self.youtube_id
        return data


def _build(raw: dict, row: int) -> BatchJob:
    url = (raw.get("url") or "").strip()
    if not url:
        raise BatchError(f"row {row}: missing url")

    try:
        start = parse_timecode(raw.get("start"))
        end = parse_timecode(raw.get("end"))
    except TimecodeError as exc:
        raise BatchError(f"row {row}: {exc}") from exc

    if end <= start:
        raise BatchError(f"row {row}: end ({end:g}s) must be after start ({start:g}s)")

    athlete = (raw.get("athlete") or "").strip() or None
    label = (raw.get("label") or "").strip() or None
    if not athlete and not label:
        raise BatchError(f"row {row}: needs an athlete or a label to name the clip")

    return BatchJob(url=url, start=start, end=end, athlete=athlete, label=label, row=row)


def _load_csv(text: str) -> list[BatchJob]:
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return []

    reader = csv.reader(lines)
    rows = [[cell.strip() for cell in row] for row in reader]

    # A header is optional; detect one so positional files also work.
    first = [cell.lower() for cell in rows[0]]
    if first and first[0] in _HEADER_HINTS:
        header, body = first, rows[1:]
        header = ["url" if h in _HEADER_HINTS else h for h in header]
    else:
        header, body = list(FIELDS), rows

    jobs = []
    for offset, row in enumerate(body, start=1):
        if not any(row):
            continue
        raw = dict(zip(header, row, strict=False))
        jobs.append(_build(raw, offset))
    return jobs


def _load_json(text: str) -> list[BatchJob]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("jobs", [])
    if not isinstance(data, list):
        raise BatchError("JSON batch file must be a list of objects, or {'jobs': [...]}")
    return [_build(item, i) for i, item in enumerate(data, start=1)]


def load_jobs(path: Path) -> list[BatchJob]:
    """Parse a batch file. `.json` is treated as JSON, anything else as CSV.

    Validates every row before returning, so a typo in the last row is caught
    before the first download begins.
    """
    text = path.read_text()
    try:
        if path.suffix.lower() == ".json":
            return _load_json(text)
        return _load_csv(text)
    except json.JSONDecodeError as exc:
        raise BatchError(f"{path}: invalid JSON: {exc}") from exc
