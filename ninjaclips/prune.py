"""Reclaim disk by deleting source videos whose clips are all confirmed.

Deleting a multi-gigabyte download is irreversible short of re-downloading, so
this module is deliberately conservative:

* A source is deletable only if at least one clip derives from it **and every
  one of those clips is confirmed** and still present on disk.
* Clips cut with `fast_proxy` block deletion — a keyframe-snapped proxy is not
  a faithful record of the source.
* Metadata sidecars (`.info.json`, `.vtt`) are kept by default: they are tiny
  and preserve provenance after the media is gone.
* Nothing is deleted without an explicit opt-in from the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .ledger import ClipRecord, iter_records

# Kept next to a pruned source so its provenance survives the media.
METADATA_SUFFIXES = (".info.json", ".vtt")

ARCHIVE_NAME = "downloaded.txt"


def forget_from_archive(downloads_dir: Path, youtube_id: str) -> bool:
    """Drop a video's line from the yt-dlp download archive.

    Deleting the media while leaving the archive entry would make a later
    re-download silently no-op ("has already been recorded in the archive"),
    breaking the one recovery path a pruned source has. Returns True if a line
    was removed.
    """
    archive = downloads_dir / ARCHIVE_NAME
    if not archive.exists() or not youtube_id:
        return False

    lines = archive.read_text().splitlines()
    # Entries are "{extractor} {id}", e.g. "youtube AIYxvfxnbz8".
    kept = [line for line in lines if line.strip().split()[-1:] != [youtube_id]]
    if len(kept) == len(lines):
        return False

    archive.write_text("".join(f"{line}\n" for line in kept))
    return True


@dataclass
class PruneCandidate:
    """One source video and the verdict on whether it can be deleted."""

    source_file: str
    youtube_id: str
    exists: bool
    size_bytes: int | None
    deletable: bool
    reason: str
    clip_count: int
    confirmed_count: int
    blocking_clips: list[str] = field(default_factory=list)
    deleted: bool = False
    archive_entry_cleared: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _evaluate(source: Path, records: list[ClipRecord]) -> PruneCandidate:
    exists = source.exists()
    size = source.stat().st_size if exists else None
    youtube_id = records[0].youtube_id if records else ""

    blocking: list[str] = []
    confirmed = 0
    for record in records:
        clip = Path(record.clip_path)
        if not clip.exists():
            blocking.append(f"{clip.name} (clip file missing)")
        elif record.encoding == "stream-copy-proxy":
            blocking.append(f"{clip.name} (stream-copy proxy, not frame-accurate)")
        elif not record.confirmed:
            blocking.append(f"{clip.name} (unconfirmed)")
        else:
            confirmed += 1

    if not exists:
        reason = "source already gone"
        deletable = False
    elif not records:
        reason = "no clips derive from this source"
        deletable = False
    elif blocking:
        reason = f"{len(blocking)} of {len(records)} clip(s) not ready"
        deletable = False
    else:
        reason = f"all {len(records)} clip(s) confirmed"
        deletable = True

    return PruneCandidate(
        source_file=str(source),
        youtube_id=youtube_id,
        exists=exists,
        size_bytes=size,
        deletable=deletable,
        reason=reason,
        clip_count=len(records),
        confirmed_count=confirmed,
        blocking_clips=blocking,
    )


def plan_prune(
    clips_dir: Path,
    downloads_dir: Path,
    youtube_id: str | None = None,
) -> list[PruneCandidate]:
    """Evaluate every source referenced by the clip ledger.

    Also reports downloaded videos that no clip references, so they show up as
    non-deletable rather than silently vanishing from the report.
    """
    by_source: dict[Path, list[ClipRecord]] = {}
    for record in iter_records(clips_dir):
        if youtube_id and record.youtube_id != youtube_id:
            continue
        by_source.setdefault(Path(record.source_file), []).append(record)

    candidates = [_evaluate(source, recs) for source, recs in sorted(by_source.items())]

    # Surface orphan downloads too — a source with no ledger entry is exactly
    # the case where a silent delete would be most surprising.
    known = set(by_source)
    if downloads_dir.exists():
        for path in sorted(downloads_dir.iterdir()):
            if path.suffix.lower() not in (".mp4", ".mkv", ".webm"):
                continue
            if path in known:
                continue
            if youtube_id and f"[{youtube_id}]" not in path.name:
                continue
            candidates.append(_evaluate(path, []))

    return candidates


def apply_prune(
    candidates: list[PruneCandidate],
    keep_metadata: bool = True,
) -> list[PruneCandidate]:
    """Delete the sources marked deletable. Mutates and returns candidates."""
    for candidate in candidates:
        if not candidate.deletable:
            continue
        source = Path(candidate.source_file)
        if not source.exists():
            continue
        source.unlink()
        candidate.deleted = True
        # Re-downloading is the only recovery a pruned source has; leaving the
        # archive entry behind would make that silently no-op.
        candidate.archive_entry_cleared = forget_from_archive(
            source.parent, candidate.youtube_id
        )

        if not keep_metadata:
            stem = source.with_suffix("")
            for suffix in METADATA_SUFFIXES:
                for sidecar in source.parent.glob(f"{stem.name}*{suffix}"):
                    sidecar.unlink()
    return candidates
