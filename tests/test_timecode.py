"""Tests for operator-typed timecode parsing."""

from __future__ import annotations

import pytest

from ninjaclips.timecode import TimecodeError, format_timecode, parse_timecode


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4243", 4243.0),          # a `t=` URL parameter
        ("1:14:55", 4495.0),       # the YouTube progress bar
        ("74:55", 4495.0),         # minutes past an hour
        ("0:30", 30.0),
        ("00:00:30", 30.0),
        ("1:00:00", 3600.0),
        ("12.5", 12.5),
        ("0:00:12.5", 12.5),
        (4243, 4243.0),
        (4243.5, 4243.5),
    ],
)
def test_parse_accepted_forms(raw, expected):
    assert parse_timecode(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "1:2:3:4",      # too many fields
        "-30",
        -30,
        "1:2.5:30",     # only seconds may be fractional
        "1:aa:30",
    ],
)
def test_parse_rejects_bad_input(raw):
    with pytest.raises(TimecodeError):
        parse_timecode(raw)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(4495, "1:14:55"), (30, "0:00:30"), (3600, "1:00:00"), (0, "0:00:00")],
)
def test_format(seconds, expected):
    assert format_timecode(seconds) == expected


def test_roundtrip():
    assert parse_timecode(format_timecode(4495)) == 4495.0
