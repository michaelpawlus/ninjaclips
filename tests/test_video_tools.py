"""Tests for manifest-driven segmentation and transform dry-runs."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ninjaclips.cli import app
from ninjaclips.video_tools import (
    cut_manifest,
    load_manifest,
    make_manifest_review_sheets,
    make_review_sheet,
    plan_heuristic_segments,
    vertical_center_crop,
    write_manifest,
)


runner = CliRunner()


def test_plan_heuristic_segments_defaults(tmp_path: Path):
    source = tmp_path / "rough run.mp4"
    source.write_bytes(b"")

    manifest = plan_heuristic_segments(source, output_prefix="esme", count=3)

    assert manifest.source == str(source)
    assert manifest.strategy == "heuristic-fixed-window"
    assert [s.start for s in manifest.segments] == [4, 17, 30]
    assert [s.duration for s in manifest.segments] == [12, 12, 12]
    assert manifest.segments[0].output_name == "esme-01-segment-01.mp4"


def test_manifest_roundtrip(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")
    path = tmp_path / "manifest.json"

    original = plan_heuristic_segments(source, count=2, label_prefix="obstacle")
    write_manifest(original, path)
    loaded = load_manifest(path)

    assert loaded.to_dict() == original.to_dict()


def test_cut_manifest_dry_run(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")
    manifest = plan_heuristic_segments(source, count=2)

    results = cut_manifest(manifest, tmp_path / "clips", dry_run=True)

    assert len(results) == 2
    assert all(r.status == "dry-run" for r in results)
    assert not (tmp_path / "clips").exists()


def test_review_sheet_dry_run(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    out = tmp_path / "sheet.jpg"

    result = make_review_sheet(source, out, dry_run=True)

    assert result.status == "dry-run"
    assert result.output_path == str(out)
    assert not out.exists()


def test_manifest_review_sheets_dry_run(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")
    manifest = plan_heuristic_segments(source, count=2, output_prefix="esme")

    results = make_manifest_review_sheets(manifest, tmp_path / "sheets", dry_run=True)

    assert len(results) == 2
    assert all(r.status == "dry-run" for r in results)
    assert results[0].input_path == str(source)
    assert results[0].output_path == str(tmp_path / "sheets" / "esme-01-segment-01.jpg")
    assert results[0].start == 4
    assert results[0].duration == 12
    assert not (tmp_path / "sheets").exists()


def test_vertical_center_crop_dry_run(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"")
    out = tmp_path / "vertical.mp4"

    result = vertical_center_crop(source, out, start=3, duration=15, dry_run=True)

    assert result.status == "dry-run"
    assert result.start == 3
    assert result.duration == 15
    assert not out.exists()


def test_segment_cli_json(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")

    result = runner.invoke(
        app,
        [
            "segment",
            str(source),
            "--count",
            "2",
            "--output-prefix",
            "esme",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source"] == str(source)
    assert len(payload["segments"]) == 2
    assert payload["segments"][1]["output_name"] == "esme-02-obstacle-02.mp4"


def test_cut_manifest_cli_dry_run_json(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")
    manifest_path = tmp_path / "segments.json"
    write_manifest(plan_heuristic_segments(source, count=1), manifest_path)

    result = runner.invoke(
        app,
        [
            "cut-manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "clips"),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["status"] == "dry-run"


def test_review_manifest_cli_dry_run_json(tmp_path: Path):
    source = tmp_path / "rough.mp4"
    source.write_bytes(b"")
    manifest_path = tmp_path / "segments.json"
    write_manifest(plan_heuristic_segments(source, count=2, output_prefix="esme"), manifest_path)

    result = runner.invoke(
        app,
        [
            "review-manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "sheets"),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 2
    assert payload[0]["status"] == "dry-run"
    assert payload[0]["output_path"].endswith("esme-01-segment-01.jpg")
    assert payload[1]["start"] == 17
