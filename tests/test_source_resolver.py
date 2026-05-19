"""Tests for ninjaclips.source_resolver — find local files by YouTube ID."""

from __future__ import annotations

from pathlib import Path

from ninjaclips.source_resolver import find_source_file, slugify, title_fragment


def test_find_source_file_returns_match(tmp_path: Path):
    target = tmp_path / "Channel - Some Title [abc123XYZ00].mp4"
    target.write_bytes(b"")
    (tmp_path / "Channel - Other Title [zzz999AAA00].mp4").write_bytes(b"")
    assert find_source_file("abc123XYZ00", tmp_path) == target


def test_find_source_file_none_when_missing(tmp_path: Path):
    (tmp_path / "Channel - Other [zzz999AAA00].mp4").write_bytes(b"")
    assert find_source_file("notfound00x", tmp_path) is None


def test_find_source_file_prefers_mp4(tmp_path: Path):
    mp4 = tmp_path / "Channel - Title [abc123XYZ00].mp4"
    mkv = tmp_path / "Channel - Title [abc123XYZ00].mkv"
    mp4.write_bytes(b"")
    mkv.write_bytes(b"")
    assert find_source_file("abc123XYZ00", tmp_path) == mp4


def test_find_source_file_no_dir(tmp_path: Path):
    assert find_source_file("abc123XYZ00", tmp_path / "nonexistent") is None


def test_find_source_file_other_extensions(tmp_path: Path):
    webm = tmp_path / "Channel - Title [abc123XYZ00].webm"
    webm.write_bytes(b"")
    assert find_source_file("abc123XYZ00", tmp_path) == webm


def test_title_fragment_extracts_title_part(tmp_path: Path):
    p = tmp_path / "Some Uploader - Stage 4 Finals [abc123XYZ00].mp4"
    p.write_bytes(b"")
    assert title_fragment(p) == "Stage_4_Finals"


def test_title_fragment_strips_unsafe_chars(tmp_path: Path):
    # yt-dlp uses U+29F8 (⧸) on Linux to replace slashes in titles, matching
    # what the real downloader writes to disk.
    p = tmp_path / "Up - T2 Preteen⧸Teen Course [abc123XYZ00].mp4"
    p.write_bytes(b"")
    frag = title_fragment(p)
    assert "⧸" not in frag
    assert "T2" in frag


def test_title_fragment_truncates_long_titles(tmp_path: Path):
    long_title = "Very " * 30 + "Long Title"
    p = tmp_path / f"Up - {long_title} [abc123XYZ00].mp4"
    p.write_bytes(b"")
    frag = title_fragment(p)
    assert len(frag) <= 60


def test_slugify_basic():
    assert slugify("Drew Drechsel") == "drew-drechsel"


def test_slugify_strips_punctuation_and_diacritics():
    assert slugify("Sébastien O'Dubé!") == "sebastien-odube"


def test_slugify_fallback_empty():
    assert slugify("!!!") == "athlete"
