"""Read athlete appearances from the WNL-Athlete-Video-Index SQLite DB.

WNL has no pip-installable Python API, so we treat its SQLite schema as a
contract and query it directly. Schema reference: WNL src/database/models.py
(Athlete, Video, AthleteAppearance).
"""

from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz


DEFAULT_DB_PATH = Path.home() / "projects" / "WNL-Athlete-Video-Index" / "data" / "wnl_athlete_video_index.db"


@dataclass
class Appearance:
    """A single athlete appearance in a source video."""

    athlete_name: str          # canonical display_name from WNL.athletes
    athlete_id: int
    youtube_id: str
    video_title: Optional[str]
    timestamp_seconds: int
    confidence: float          # WNL's confidence_score for the appearance
    match_score: float         # rapidfuzz score for athlete_query → athlete name/alias


@dataclass
class IndexStatus:
    """Readiness check for clipping one athlete from one WNL-indexed video."""

    status: str
    ready: bool
    athlete_query: str
    youtube_id: str
    db_path: str
    message: str
    video_exists: bool = False
    video_title: Optional[str] = None
    athlete: Optional[str] = None
    athlete_id: Optional[int] = None
    matches: Optional[list[dict]] = None
    appearances: Optional[list[dict]] = None

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_db_path(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return explicit
    env = os.environ.get("WNL_DB_PATH")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DB_PATH


# Lifted from WNL src/search/fuzzy.py:_normalize — too small to import, also
# WNL isn't pip-installable so we can't.
def _normalize(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    ).lower()


def _score(query: str, name: str) -> float:
    """Score query against a name using the same strategy as WNL fuzzy_search."""
    q = _normalize(query)
    n = _normalize(name)
    return max(
        fuzz.ratio(q, n),
        fuzz.partial_ratio(q, n),
        fuzz.token_set_ratio(q, n),
    )


@dataclass
class AthleteMatch:
    athlete_id: int
    display_name: str
    matched_on: str
    score: float


def _fuzzy_match_athletes(
    conn: sqlite3.Connection,
    query: str,
    threshold: float = 70.0,
) -> list[AthleteMatch]:
    """Return all athletes matching query above threshold, sorted by score desc.

    Matches against both display_name and any aliases (JSON array column).
    Deduplicates by athlete_id, keeping the best match per athlete.
    """
    rows = conn.execute("SELECT id, display_name, aliases FROM athletes").fetchall()

    best: dict[int, AthleteMatch] = {}
    for row in rows:
        athlete_id, display_name, aliases_raw = row
        candidates = [display_name]
        if aliases_raw:
            try:
                aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else aliases_raw
                if isinstance(aliases, list):
                    candidates.extend(str(a) for a in aliases)
            except (json.JSONDecodeError, TypeError):
                pass

        for cand in candidates:
            score = _score(query, cand)
            if score < threshold:
                continue
            existing = best.get(athlete_id)
            if existing is None or score > existing.score:
                best[athlete_id] = AthleteMatch(
                    athlete_id=athlete_id,
                    display_name=display_name,
                    matched_on=cand,
                    score=score,
                )

    return sorted(best.values(), key=lambda m: m.score, reverse=True)


def find_appearances(
    athlete_query: str,
    db_path: Optional[Path] = None,
    threshold: float = 70.0,
    exact_top_only: bool = True,
) -> tuple[list[Appearance], list[AthleteMatch]]:
    """Resolve an athlete query → list of Appearance rows.

    Returns (appearances, matches) where matches is the full ranked list of
    athlete matches. If exact_top_only and the top match score is meaningfully
    above the others, only the top athlete's appearances are returned.

    Raises FileNotFoundError if the WNL DB is missing.
    """
    path = resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        matches = _fuzzy_match_athletes(conn, athlete_query, threshold=threshold)
        if not matches:
            return [], []

        # If the top match is a clear winner (>=5 points clear), use only it.
        # Otherwise return all matches above threshold and let caller decide
        # whether the query is ambiguous.
        top = matches[0]
        if exact_top_only and (len(matches) == 1 or top.score - matches[1].score >= 5):
            chosen = [top]
        else:
            chosen = matches

        if len(chosen) > 1:
            # Caller should treat this as ambiguous; don't return appearances.
            return [], matches

        athlete_id = chosen[0].athlete_id
        rows = conn.execute(
            """
            SELECT
                a.display_name,
                a.id,
                v.youtube_id,
                v.title,
                ap.timestamp_seconds,
                ap.confidence_score
            FROM athlete_appearances ap
            JOIN athletes a ON a.id = ap.athlete_id
            JOIN videos v ON v.id = ap.video_id
            WHERE ap.athlete_id = ?
            ORDER BY v.youtube_id, ap.timestamp_seconds
            """,
            (athlete_id,),
        ).fetchall()

        appearances = [
            Appearance(
                athlete_name=r[0],
                athlete_id=r[1],
                youtube_id=r[2],
                video_title=r[3],
                timestamp_seconds=int(r[4]),
                confidence=float(r[5]) if r[5] is not None else 1.0,
                match_score=top.score,
            )
            for r in rows
        ]
        return appearances, matches
    finally:
        conn.close()


def check_index_status(
    athlete_query: str,
    youtube_id: str,
    db_path: Optional[Path] = None,
    threshold: float = 70.0,
) -> IndexStatus:
    """Return whether WNL has an athlete appearance for a specific YouTube ID."""
    path = resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        matches = _fuzzy_match_athletes(conn, athlete_query, threshold=threshold)
        match_dicts = [
            {"display_name": m.display_name, "matched_on": m.matched_on, "score": m.score}
            for m in matches
        ]

        video_row = conn.execute(
            "SELECT id, title FROM videos WHERE youtube_id = ?",
            (youtube_id,),
        ).fetchone()
        video_exists = video_row is not None
        video_title = str(video_row[1]) if video_row and video_row[1] is not None else None

        if not matches:
            return IndexStatus(
                status="athlete_missing",
                ready=False,
                athlete_query=athlete_query,
                youtube_id=youtube_id,
                db_path=str(path),
                message=f"No athlete in WNL matched '{athlete_query}'.",
                video_exists=video_exists,
                video_title=video_title,
                matches=[],
                appearances=[],
            )

        top = matches[0]
        if len(matches) > 1 and top.score - matches[1].score < 5:
            return IndexStatus(
                status="ambiguous_athlete",
                ready=False,
                athlete_query=athlete_query,
                youtube_id=youtube_id,
                db_path=str(path),
                message=f"Ambiguous athlete query: matched {len(matches)} athletes.",
                video_exists=video_exists,
                video_title=video_title,
                matches=match_dicts,
                appearances=[],
            )

        if not video_exists:
            return IndexStatus(
                status="video_missing",
                ready=False,
                athlete_query=athlete_query,
                youtube_id=youtube_id,
                db_path=str(path),
                message=f"Video {youtube_id} is not in the WNL index yet.",
                video_exists=False,
                athlete=top.display_name,
                athlete_id=top.athlete_id,
                matches=match_dicts,
                appearances=[],
            )

        rows = conn.execute(
            """
            SELECT ap.timestamp_seconds, ap.confidence_score
            FROM athlete_appearances ap
            WHERE ap.athlete_id = ? AND ap.video_id = ?
            ORDER BY ap.timestamp_seconds
            """,
            (top.athlete_id, video_row[0]),
        ).fetchall()
        appearances = [
            {
                "timestamp_seconds": int(timestamp),
                "confidence": float(confidence) if confidence is not None else 1.0,
            }
            for timestamp, confidence in rows
        ]

        if not appearances:
            return IndexStatus(
                status="appearance_missing",
                ready=False,
                athlete_query=athlete_query,
                youtube_id=youtube_id,
                db_path=str(path),
                message=(
                    f"WNL has video {youtube_id}, but no appearance for "
                    f"{top.display_name} in that video."
                ),
                video_exists=True,
                video_title=video_title,
                athlete=top.display_name,
                athlete_id=top.athlete_id,
                matches=match_dicts,
                appearances=[],
            )

        return IndexStatus(
            status="ready",
            ready=True,
            athlete_query=athlete_query,
            youtube_id=youtube_id,
            db_path=str(path),
            message=(
                f"WNL has {len(appearances)} appearance(s) for "
                f"{top.display_name} in {youtube_id}."
            ),
            video_exists=True,
            video_title=video_title,
            athlete=top.display_name,
            athlete_id=top.athlete_id,
            matches=match_dicts,
            appearances=appearances,
        )
    finally:
        conn.close()
