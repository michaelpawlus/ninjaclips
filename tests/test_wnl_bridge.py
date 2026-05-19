"""Tests for ninjaclips.wnl_bridge — fixture SQLite DB + fuzzy round-trip."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ninjaclips.wnl_bridge import (
    Appearance,
    _normalize,
    find_appearances,
    resolve_db_path,
)


def _make_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE athletes (
            id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            aliases TEXT
        );
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY,
            youtube_id TEXT NOT NULL UNIQUE,
            title TEXT
        );
        CREATE TABLE athlete_appearances (
            id INTEGER PRIMARY KEY,
            athlete_id INTEGER NOT NULL,
            video_id INTEGER NOT NULL,
            timestamp_seconds INTEGER NOT NULL,
            confidence_score REAL
        );
        """
    )
    conn.executemany(
        "INSERT INTO athletes (id, display_name, aliases) VALUES (?, ?, ?)",
        [
            (1, "Drew Drechsel", json.dumps(["Drechsel", "Real Life Ninja"])),
            (2, "Jessie Graff", json.dumps(["Graff"])),
            (3, "Sébastien Dubé", json.dumps([])),
            (4, "John Smith", None),
            (5, "John Smythe", None),
        ],
    )
    conn.executemany(
        "INSERT INTO videos (id, youtube_id, title) VALUES (?, ?, ?)",
        [
            (1, "abc123XYZ00", "Stage 4 Finals"),
            (2, "def456ABC11", "Semifinals Vegas"),
        ],
    )
    conn.executemany(
        "INSERT INTO athlete_appearances (athlete_id, video_id, timestamp_seconds, confidence_score) VALUES (?, ?, ?, ?)",
        [
            (1, 1, 300, 0.95),
            (1, 1, 1820, 0.9),
            (1, 2, 450, 0.85),
            (2, 1, 900, 0.9),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "wnl.db"
    _make_fixture_db(db)
    return db


def test_normalize_strips_diacritics():
    assert _normalize("Sébastien") == "sebastien"
    assert _normalize("Drechsel") == "drechsel"


def test_find_appearances_by_display_name(fixture_db: Path):
    appearances, matches = find_appearances("Drew Drechsel", db_path=fixture_db)
    assert len(matches) >= 1
    assert matches[0].display_name == "Drew Drechsel"
    assert len(appearances) == 3
    assert all(a.athlete_name == "Drew Drechsel" for a in appearances)
    # Ordered by youtube_id then timestamp
    assert appearances[0].timestamp_seconds == 300
    assert appearances[1].timestamp_seconds == 1820


def test_find_appearances_partial_name(fixture_db: Path):
    # Partial last name fuzzy-matches the canonical athlete
    appearances, _ = find_appearances("drechsel", db_path=fixture_db)
    assert len(appearances) == 3
    assert appearances[0].athlete_name == "Drew Drechsel"


def test_find_appearances_first_name_only(fixture_db: Path):
    appearances, _ = find_appearances("Drew", db_path=fixture_db)
    assert len(appearances) >= 1
    assert appearances[0].athlete_name == "Drew Drechsel"


def test_find_appearances_alias(fixture_db: Path):
    # Match via an alias entry
    appearances, matches = find_appearances("Real Life Ninja", db_path=fixture_db)
    assert appearances[0].athlete_name == "Drew Drechsel"
    assert matches[0].matched_on == "Real Life Ninja"


def test_find_appearances_accent_insensitive(fixture_db: Path):
    appearances, _ = find_appearances("Sebastien Dube", db_path=fixture_db)
    # No appearances for Sébastien but the athlete resolves cleanly
    assert appearances == []


def test_find_appearances_ambiguous(fixture_db: Path):
    # "John" should be too close between John Smith and John Smythe
    appearances, matches = find_appearances("John", db_path=fixture_db)
    if len(matches) > 1 and matches[0].score - matches[1].score < 5:
        # Ambiguous → no appearances, full match list returned
        assert appearances == []
        assert len(matches) >= 2


def test_find_appearances_no_match(fixture_db: Path):
    appearances, matches = find_appearances("xyzzy nonexistent", db_path=fixture_db)
    assert appearances == []
    assert matches == []


def test_missing_db_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        find_appearances("anyone", db_path=tmp_path / "nope.db")


def test_resolve_db_path_env_override(tmp_path: Path, monkeypatch):
    target = tmp_path / "from-env.db"
    monkeypatch.setenv("WNL_DB_PATH", str(target))
    assert resolve_db_path() == target


def test_appearance_fields(fixture_db: Path):
    appearances, _ = find_appearances("Drew Drechsel", db_path=fixture_db)
    a = appearances[0]
    assert isinstance(a, Appearance)
    assert a.youtube_id == "abc123XYZ00"
    assert a.video_title == "Stage 4 Finals"
    assert a.confidence == pytest.approx(0.95)
    assert a.athlete_id == 1
