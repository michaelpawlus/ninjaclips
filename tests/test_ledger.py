"""Tests for the clip provenance ledger."""

from __future__ import annotations

import json
from pathlib import Path

from ninjaclips.ledger import (
    ClipRecord,
    iter_records,
    ledger_path_for,
    mark_confirmed,
    read_record,
    write_record,
)


def _record(tmp_path: Path, name: str = "clip", **overrides) -> ClipRecord:
    defaults = dict(
        clip_path=str(tmp_path / f"{name}.mp4"),
        source_file=str(tmp_path / "source.webm"),
        youtube_id="AIYxvfxnbz8",
        source_start=4238.0,
        duration=120.0,
        origin="manual",
    )
    defaults.update(overrides)
    return ClipRecord(**defaults)


def test_sidecar_sits_next_to_clip(tmp_path: Path):
    assert ledger_path_for(tmp_path / "a.mp4") == tmp_path / "a.json"


def test_write_then_read_roundtrip(tmp_path: Path):
    record = _record(tmp_path, athlete="Esme Newton-Pawlus")
    write_record(record)

    loaded = read_record(tmp_path / "clip.mp4")
    assert loaded is not None
    assert loaded.youtube_id == "AIYxvfxnbz8"
    assert loaded.source_start == 4238.0
    assert loaded.athlete == "Esme Newton-Pawlus"
    assert loaded.confirmed is False


def test_read_missing_sidecar_returns_none(tmp_path: Path):
    assert read_record(tmp_path / "nope.mp4") is None


def test_read_corrupt_sidecar_returns_none(tmp_path: Path):
    (tmp_path / "bad.json").write_text("{not json")
    assert read_record(tmp_path / "bad.mp4") is None


def test_unknown_fields_are_ignored(tmp_path: Path):
    """A sidecar from a future version must not crash an older reader."""
    payload = _record(tmp_path).to_dict()
    payload["some_future_field"] = "whatever"
    (tmp_path / "clip.json").write_text(json.dumps(payload))

    loaded = read_record(tmp_path / "clip.mp4")
    assert loaded is not None
    assert loaded.youtube_id == "AIYxvfxnbz8"


def test_mark_confirmed_sets_flag_and_timestamp(tmp_path: Path):
    write_record(_record(tmp_path))

    confirmed = mark_confirmed(tmp_path / "clip.mp4")
    assert confirmed.confirmed is True
    assert confirmed.confirmed_utc is not None

    reloaded = read_record(tmp_path / "clip.mp4")
    assert reloaded.confirmed is True


def test_unconfirm_clears_timestamp(tmp_path: Path):
    write_record(_record(tmp_path))
    mark_confirmed(tmp_path / "clip.mp4")

    cleared = mark_confirmed(tmp_path / "clip.mp4", confirmed=False)
    assert cleared.confirmed is False
    assert cleared.confirmed_utc is None


def test_mark_confirmed_without_sidecar_returns_none(tmp_path: Path):
    assert mark_confirmed(tmp_path / "ghost.mp4") is None


def test_iter_records_skips_foreign_json(tmp_path: Path):
    write_record(_record(tmp_path, name="one"))
    write_record(_record(tmp_path, name="two"))
    # A segment manifest also lives as .json but is not a ledger entry.
    (tmp_path / "manifest.json").write_text(json.dumps({"source": "x", "segments": []}))

    records = iter_records(tmp_path)
    assert len(records) == 2
    assert {Path(r.clip_path).stem for r in records} == {"one", "two"}


def test_iter_records_on_missing_dir(tmp_path: Path):
    assert iter_records(tmp_path / "absent") == []
