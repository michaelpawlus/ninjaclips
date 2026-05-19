"""Map a YouTube ID to a local source file in the downloads directory.

The downloader (download.py) writes files named:
    {uploader} - {title} [{id}].{ext}

So we glob for `*[{id}].mp4` (and a few other extensions) to recover the path.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Optional


VIDEO_EXTS = (".mp4", ".mkv", ".webm")


def _strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def find_source_file(youtube_id: str, downloads_dir: Path) -> Optional[Path]:
    """Return the source video for a YouTube ID, or None if not downloaded.

    Matches any file with `[{youtube_id}]` in the basename and a known video
    extension. If multiple files match (unlikely but possible — same ID could
    appear in different containers), prefer .mp4, then the first found.
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

    matches.sort(key=lambda p: (0 if p.suffix == ".mp4" else 1, p.name))
    return matches[0]


def title_fragment(source_file: Path) -> str:
    """Extract a short, slug-friendly fragment from the source filename.

    The filename is `{uploader} - {title} [{id}].mp4`. We pull the title
    portion, truncate, and replace filesystem-hostile characters with `_`.
    """
    stem = source_file.stem
    # Drop the trailing ` [id]`
    if " [" in stem:
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
