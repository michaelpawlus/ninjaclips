"""Tests for prune safety.

These matter more than most: a false positive here permanently deletes
multi-gigabyte downloads.
"""

from __future__ import annotations

from pathlib import Path

from ninjaclips.ledger import ClipRecord, mark_confirmed, write_record
from ninjaclips.prune import apply_prune, plan_prune


def _setup(tmp_path: Path):
    clips = tmp_path / "clips"
    downloads = tmp_path / "downloads"
    clips.mkdir()
    downloads.mkdir()
    return clips, downloads


def _source(downloads: Path, youtube_id: str = "AIYxvfxnbz8") -> Path:
    path = downloads / f"WNL - Course [{youtube_id}].webm"
    path.write_bytes(b"\x00" * 2048)
    return path


def _clip(clips: Path, source: Path, name: str, **overrides) -> Path:
    clip_path = clips / f"{name}.mp4"
    clip_path.write_bytes(b"\x00" * 64)
    defaults = dict(
        clip_path=str(clip_path),
        source_file=str(source),
        youtube_id="AIYxvfxnbz8",
        source_start=4238.0,
        duration=120.0,
        origin="manual",
        encoding="cfr-reencode",
    )
    defaults.update(overrides)
    write_record(ClipRecord(**defaults))
    return clip_path


def test_unconfirmed_clip_blocks_deletion(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    _clip(clips, source, "esme")

    [candidate] = [c for c in plan_prune(clips, downloads) if c.source_file == str(source)]
    assert candidate.deletable is False
    assert "unconfirmed" in candidate.blocking_clips[0]


def test_confirmed_clip_allows_deletion(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    clip_path = _clip(clips, source, "esme")
    mark_confirmed(clip_path)

    [candidate] = [c for c in plan_prune(clips, downloads) if c.source_file == str(source)]
    assert candidate.deletable is True
    assert candidate.confirmed_count == 1


def test_one_unconfirmed_sibling_blocks_the_whole_source(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    mark_confirmed(_clip(clips, source, "esme"))
    _clip(clips, source, "sibling")  # left unconfirmed

    [candidate] = [c for c in plan_prune(clips, downloads) if c.source_file == str(source)]
    assert candidate.deletable is False


def test_stream_copy_proxy_blocks_deletion_even_when_confirmed(tmp_path: Path):
    """A keyframe-snapped proxy is not a faithful record of the source."""
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    clip_path = _clip(clips, source, "proxy", encoding="stream-copy-proxy")
    mark_confirmed(clip_path)

    [candidate] = [c for c in plan_prune(clips, downloads) if c.source_file == str(source)]
    assert candidate.deletable is False
    assert "proxy" in candidate.blocking_clips[0]


def test_missing_clip_file_blocks_deletion(tmp_path: Path):
    """Sidecar says confirmed, but the clip was deleted — keep the source."""
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    clip_path = _clip(clips, source, "esme")
    mark_confirmed(clip_path)
    clip_path.unlink()

    [candidate] = [c for c in plan_prune(clips, downloads) if c.source_file == str(source)]
    assert candidate.deletable is False
    assert "missing" in candidate.blocking_clips[0]


def test_orphan_download_is_reported_but_never_deletable(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)

    [candidate] = plan_prune(clips, downloads)
    assert candidate.source_file == str(source)
    assert candidate.deletable is False
    assert candidate.clip_count == 0


def test_video_filter_scopes_the_plan(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    _source(downloads, "AAAAAAAAAAA")
    _source(downloads, "BBBBBBBBBBB")

    candidates = plan_prune(clips, downloads, youtube_id="AAAAAAAAAAA")
    assert len(candidates) == 1
    assert "AAAAAAAAAAA" in candidates[0].source_file


def test_apply_prune_deletes_source_and_keeps_metadata(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    info = source.with_suffix(".info.json")
    info.write_text("{}")
    mark_confirmed(_clip(clips, source, "esme"))

    candidates = plan_prune(clips, downloads)
    apply_prune([c for c in candidates if c.deletable])

    assert not source.exists()
    assert info.exists(), "metadata sidecar should survive by default"


def test_apply_prune_never_touches_blocked_sources(tmp_path: Path):
    clips, downloads = _setup(tmp_path)
    source = _source(downloads)
    _clip(clips, source, "esme")  # unconfirmed

    candidates = plan_prune(clips, downloads)
    apply_prune([c for c in candidates if c.deletable])

    assert source.exists()
