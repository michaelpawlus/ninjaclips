"""Map a YouTube ID to a local source file in the downloads directory.

The downloader (download.py) writes files named:
    {uploader} - {title} [{id}].{ext}

So we glob for `*[{id}].mp4` (and a few other extensions) to recover the path.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

VIDEO_EXTS = (".mp4", ".mkv", ".webm")


def _strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def find_source_file(youtube_id: str, downloads_dir: Path) -> Path | None:
    """Return the source video for a YouTube ID, or None if not downloaded.

    Matches any file with `[{youtube_id}]` in the basename and a known video
    extension. When several match, prefer a full download over a partial
    (`--sections`) one — a partial covers only part of the timeline, so a cut
    outside its window would fail against it but succeed against the full file.
    Then prefer .mp4, then sort by name for determinism.
    """
    if not downloads_dir.exists():
        return None

    matches: list[Path] = []
    needle = f"[{youtube_id}]"
    for ext in VIDEO_EXTS:
        for p in downloads_dir.glob(f"*{ext}"):
            if needle in p.name:
                matches.append(p)

    if not matches:
        return None

    matches.sort(
        key=lambda p: (
            1 if p.with_suffix(".section.json").exists() else 0,
            0 if p.suffix == ".mp4" else 1,
            p.name,
        )
    )
    return matches[0]


def section_offset(source_file: Path) -> float:
    """Seconds between the source's t=0 and this file's t=0.

    Zero for a full download. For a partial (`--sections`) download, the value
    recorded in its `.section.json` sidecar. Callers seeking to a
    source-absolute timestamp must subtract this from the seek position.
    """
    sidecar = source_file.with_suffix(".section.json")
    if not sidecar.exists():
        return 0.0
    try:
        data = json.loads(sidecar.read_text())
    except json.JSONDecodeError:
        return 0.0
    try:
        return float(data.get("section_start") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def title_fragment(source_file: Path) -> str:
    """Extract a short, slug-friendly fragment from the source filename.

    The filename is `{uploader} - {title} [{id}].mp4`. We pull the title
    portion, truncate, and replace filesystem-hostile characters with `_`.
    """
    stem = source_file.stem
    # Drop every trailing ` [...]` group — a full download ends with ` [id]`,
    # a partial one with ` [id] [sec START-END]`.
    while stem.rstrip().endswith("]") and " [" in stem:
        stem = stem.rsplit(" [", 1)[0]
    # Drop the leading `uploader - `
    if " - " in stem:
        stem = stem.split(" - ", 1)[1]

    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in stem)
    safe = safe.strip().replace(" ", "_")
    return safe[:60] or "video"


def slugify(name: str) -> str:
    """Lowercase, ASCII-safe slug for athlete names."""
    ascii_safe = _strip_diacritics(name)
    safe = "".join(c if c.isalnum() or c in " -" else "" for c in ascii_safe)
    return safe.strip().lower().replace(" ", "-") or "athlete"
