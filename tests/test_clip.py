"""Tests for ninjaclips.clip — dry-run + idempotency without invoking ffmpeg."""

from __future__ import annotations

from pathlib import Path

from ninjaclips.clip import ClipResult, rough_cut


def test_dry_run_skips_ffmpeg(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    out = tmp_path / "out.mp4"
    result = rough_cut(
        source_file=source,
        output_path=out,
        youtube_id="abc",
        athlete="Drew Drechsel",
        start=10,
        duration=90,
        dry_run=True,
    )
    assert isinstance(result, ClipResult)
    assert result.status == "dry-run"
    assert result.encoding == "dry-run"
    assert result.file_size_bytes is None
    assert not out.exists()


def test_existing_file_skipped(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    out = tmp_path / "existing.mp4"
    out.write_bytes(b"\x00" * 1234)
    result = rough_cut(
        source_file=source,
        output_path=out,
        youtube_id="abc",
        athlete="Drew Drechsel",
        start=10,
        duration=90,
    )
    assert result.status == "exists"
    assert result.encoding == "skipped"
    assert result.file_size_bytes == 1234


def test_result_dict_roundtrip(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"")
    out = tmp_path / "out.mp4"
    result = rough_cut(
        source_file=source,
        output_path=out,
        youtube_id="abc",
        athlete="Drew Drechsel",
        start=10,
        duration=90,
        dry_run=True,
    )
    d = result.to_dict()
    assert d["source_video_id"] == "abc"
    assert d["athlete"] == "Drew Drechsel"
    assert d["start"] == 10
    assert d["duration"] == 90
