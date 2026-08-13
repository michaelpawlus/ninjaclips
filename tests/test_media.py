"""Tests for ffprobe parsing and frame-rate selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ninjaclips import media
from ninjaclips.media import _parse_rate, probe_video


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30/1", 30.0),
        ("30000/1001", pytest.approx(29.97, abs=0.01)),
        ("60", 60.0),
        ("0/0", None),
        ("N/A", None),
        ("", None),
        (None, None),
        ("1/0", None),
        ("garbage", None),
    ],
)
def test_parse_rate(raw, expected):
    assert _parse_rate(raw) == expected


def _fake_probe(monkeypatch, r_rate: str, avg_rate: str):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 2560,
                "height": 1440,
                "codec_name": "vp9",
                "r_frame_rate": r_rate,
                "avg_frame_rate": avg_rate,
            },
            {"codec_type": "audio", "codec_name": "opus"},
        ],
        "format": {"duration": "7200.5"},
    }
    monkeypatch.setattr(media, "resolve_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(media, "run", lambda args: (0, json.dumps(payload)))


def test_cfr_source_encodes_against_nominal_rate(monkeypatch):
    """A measured 29.999998 must not become the encode target."""
    _fake_probe(monkeypatch, r_rate="30/1", avg_rate="29.999998")

    info = probe_video(Path("x.mp4"))
    assert info.is_vfr is False
    assert info.fps == 30.0
    assert info.measured_fps == pytest.approx(29.999998)
    assert info.nominal_fps == 30.0


def test_vfr_source_falls_back_to_measured_rate(monkeypatch):
    """VFR has no meaningful nominal rate, so the average is the honest target."""
    _fake_probe(monkeypatch, r_rate="60/1", avg_rate="24/1")

    info = probe_video(Path("x.mp4"))
    assert info.is_vfr is True
    assert info.fps == 24.0


def test_probe_reads_dimensions_and_codecs(monkeypatch):
    _fake_probe(monkeypatch, r_rate="30/1", avg_rate="30/1")

    info = probe_video(Path("x.mp4"))
    assert (info.width, info.height) == (2560, 1440)
    assert info.vcodec == "vp9"
    assert info.acodec == "opus"
    assert info.duration == pytest.approx(7200.5)


def test_probe_raises_on_ffprobe_failure(monkeypatch):
    monkeypatch.setattr(media, "resolve_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(media, "run", lambda args: (1, "not a media file"))

    with pytest.raises(RuntimeError, match="ffprobe failed"):
        probe_video(Path("x.mp4"))
