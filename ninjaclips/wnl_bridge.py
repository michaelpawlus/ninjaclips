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
from dataclasses import dataclass
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
