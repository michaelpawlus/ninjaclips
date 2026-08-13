"""Provenance sidecars linking every rough cut back to its source video.

`prune` deletes source media, so it must never guess. Each rough cut writes a
JSON sidecar next to itself recording exactly which file it came from, at which
source-absolute timestamp, and whether a human has confirmed the cut is
correct. `prune` deletes a source only when every clip derived from it is
confirmed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LEDGER_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ClipRecord:
    """Everything needed to re-cut this clip, or to safely delete its source."""

    clip_path: str
    source_file: str
    youtube_id: str
    # Source-absolute seconds — measured from the start of the original video,
    # never from the start of an intermediate clip.
    source_start: float
    duration: float
    origin: str  # "wnl" | "manual" | "batch"
    # Where source_file itself starts within the original video. Non-zero only
    # for a partial (`--sections`) download.
    source_offset: float = 0.0
    athlete: str | None = None
    label: str | None = None
    wnl_timestamp: int | None = None
    pre_pad: float | None = None
    source_fps: float | None = None
    source_width: int | None = None
    source_height: int | None = None
    source_was_vfr: bool | None = None
    output_fps: float | None = None
    encoding: str | None = None
    ffmpeg_cmd: list[str] = field(default_factory=list)
    created_utc: str = field(default_factory=_utc_now)
    confirmed: bool = False
    confirmed_utc: str | None = None
    version: int = LEDGER_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def ledger_path_for(clip_path: Path) -> Path:
    """Sidecar path for a clip: `foo.mp4` -> `foo.json`."""
    return clip_path.with_suffix(".json")


def write_record(record: ClipRecord) -> Path:
    path = ledger_path_for(Path(record.clip_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2) + "\n")
    return path


def read_record(clip_path: Path) -> ClipRecord | None:
    """Load a clip's sidecar, or None if absent or unreadable."""
    path = ledger_path_for(clip_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    known = {f for f in ClipRecord.__dataclass_fields__}
    return ClipRecord(**{k: v for k, v in data.items() if k in known})


def iter_records(clips_dir: Path) -> list[ClipRecord]:
    """Load every clip sidecar in a directory, sorted by clip path."""
    if not clips_dir.exists():
        return []
    records = []
    for sidecar in sorted(clips_dir.glob("*.json")):
        try:
            data = json.loads(sidecar.read_text())
        except json.JSONDecodeError:
            continue
        if "clip_path" not in data or "source_file" not in data:
            continue  # not one of ours
        known = {f for f in ClipRecord.__dataclass_fields__}
        records.append(ClipRecord(**{k: v for k, v in data.items() if k in known}))
    return records


def mark_confirmed(clip_path: Path, confirmed: bool = True) -> ClipRecord | None:
    """Flip a clip's confirmed flag. Returns None if it has no sidecar."""
    record = read_record(clip_path)
    if record is None:
        return None
    record.confirmed = confirmed
    record.confirmed_utc = _utc_now() if confirmed else None
    write_record(record)
    return record
