"""Tests for the partial-download offset.

A sectioned file's t=0 is not the source's t=0. If the offset is ever dropped,
every cut against that file silently lands `section_start` seconds early — the
exact class of bug this project exists to eliminate.
"""

from __future__ import annotations

import json
from pathlib import Path

from ninjaclips.clip import rough_cut
from ninjaclips.download import write_section_sidecar
from ninjaclips.ledger import read_record
from ninjaclips.source_resolver import section_offset, title_fragment


def test_full_download_has_no_offset(tmp_path: Path):
    source = tmp_path / "WNL - Course [abc].mp4"
    source.write_bytes(b"")
    assert section_offset(source) == 0.0


def test_sidecar_supplies_offset(tmp_path: Path):
    source = tmp_path / "WNL - Course [abc] [sec 4140-4560].mp4"
    source.write_bytes(b"")
    write_section_sidecar(source, "abc", 4140.0, 4560.0)

    assert section_offset(source) == 4140.0


def test_sidecar_lands_beside_the_media(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    sidecar = write_section_sidecar(source, "abc", 100.0, 200.0)

    assert sidecar == tmp_path / "clip.section.json"
    assert json.loads(sidecar.read_text())["section_start"] == 100.0


def test_corrupt_sidecar_falls_back_to_zero(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    (tmp_path / "clip.section.json").write_text("{not json")

    assert section_offset(source) == 0.0


def test_title_fragment_strips_section_marker(tmp_path: Path):
    full = Path("WNL Community - Mlab Ohio T2 Course [abc].mp4")
    partial = Path("WNL Community - Mlab Ohio T2 Course [abc] [sec 4140-4560].mp4")
    assert title_fragment(full) == title_fragment(partial)


def test_full_download_preferred_over_partial(tmp_path: Path):
    """A partial covers only part of the timeline; the full file always works."""
    from ninjaclips.source_resolver import find_source_file

    partial = tmp_path / "WNL - Course [abc] [sec 4200-4320].mp4"
    partial.write_bytes(b"")
    write_section_sidecar(partial, "abc", 4200.0, 4320.0)
    full = tmp_path / "WNL - Course [abc].mp4"
    full.write_bytes(b"")

    assert find_source_file("abc", tmp_path) == full


def test_partial_used_when_it_is_the_only_copy(tmp_path: Path):
    from ninjaclips.source_resolver import find_source_file

    partial = tmp_path / "WNL - Course [abc] [sec 4200-4320].mp4"
    partial.write_bytes(b"")
    write_section_sidecar(partial, "abc", 4200.0, 4320.0)

    assert find_source_file("abc", tmp_path) == partial


def test_start_before_section_is_an_error_not_a_bad_cut(tmp_path: Path):
    """Asking for a time the partial file does not contain must fail loudly."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    out = tmp_path / "out.mp4"

    result = rough_cut(
        source_file=source,
        output_path=out,
        youtube_id="abc",
        start=100.0,
        duration=10.0,
        source_offset=4140.0,
    )
    assert result.status == "error"
    assert "partial section" in result.error
    assert not out.exists()


def test_ledger_records_source_absolute_start_with_offset(tmp_path: Path, monkeypatch):
    """The ledger must store source-absolute time, not file-relative time."""
    import ninjaclips.clip as clip_module
    from ninjaclips.media import MediaInfo

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    out = tmp_path / "out.mp4"

    info = MediaInfo(
        path=str(source),
        duration=420.0,
        width=1920,
        height=1080,
        fps=30.0,
        nominal_fps=30.0,
        measured_fps=30.0,
        is_vfr=False,
        vcodec="vp9",
        acodec="opus",
    )

    def fake_run(cmd):
        out.write_bytes(b"\x00" * 10)
        return 0, ""

    monkeypatch.setattr(clip_module, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(clip_module, "run", fake_run)

    result = rough_cut(
        source_file=source,
        output_path=out,
        youtube_id="abc",
        start=4243.0,
        duration=60.0,
        info=info,
        source_offset=4140.0,
    )
    assert result.status == "created"

    record = read_record(out)
    assert record.source_start == 4243.0, "ledger must stay source-absolute"
    assert record.source_offset == 4140.0
    # ffmpeg itself must have been told the file-relative position.
    assert "103.000" in record.ffmpeg_cmd
