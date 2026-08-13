"""Tests for batch job parsing.

Validation runs before any download, so these tests guard the operator against
discovering a typo an hour into a multi-gigabyte fetch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ninjaclips.batch import BatchError, BatchJob, load_jobs


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=AIYxvfxnbz8", "AIYxvfxnbz8"),
        ("https://www.youtube.com/live/AIYxvfxnbz8?si=xyz&t=4243", "AIYxvfxnbz8"),
        ("https://youtu.be/AIYxvfxnbz8?t=4243", "AIYxvfxnbz8"),
        ("https://www.youtube.com/watch?v=AIYxvfxnbz8&list=PL123", "AIYxvfxnbz8"),
        ("https://www.youtube.com/shorts/AIYxvfxnbz8", "AIYxvfxnbz8"),
        ("AIYxvfxnbz8", "AIYxvfxnbz8"),
    ],
)
def test_youtube_id_extraction(url, expected):
    assert BatchJob(url=url, start=0, end=1, label="x").youtube_id == expected


def test_csv_with_header(tmp_path: Path):
    path = _write(
        tmp_path,
        "jobs.csv",
        "url,start,end,athlete\n"
        "https://www.youtube.com/watch?v=AIYxvfxnbz8,1:10:43,1:14:55,Esme Newton-Pawlus\n",
    )
    [job] = load_jobs(path)
    assert job.youtube_id == "AIYxvfxnbz8"
    assert job.start == 4243.0
    assert job.end == 4495.0
    assert job.athlete == "Esme Newton-Pawlus"


def test_csv_without_header_and_with_comments(tmp_path: Path):
    path = _write(
        tmp_path,
        "jobs.csv",
        "# season 2025 archive\n"
        "\n"
        "AIYxvfxnbz8,4243,4495,Esme Newton-Pawlus\n"
        "9C8L1tQaYgs,0:30,2:00,Someone Else\n",
    )
    jobs = load_jobs(path)
    assert len(jobs) == 2
    assert jobs[1].start == 30.0
    assert jobs[1].end == 120.0


def test_json_list(tmp_path: Path):
    path = _write(
        tmp_path,
        "jobs.json",
        json.dumps(
            [{"url": "AIYxvfxnbz8", "start": "1:10:43", "end": "1:14:55", "label": "t2"}]
        ),
    )
    [job] = load_jobs(path)
    assert job.label == "t2"
    assert job.end == 4495.0


def test_json_wrapped_in_jobs_key(tmp_path: Path):
    path = _write(
        tmp_path,
        "jobs.json",
        json.dumps({"jobs": [{"url": "X", "start": 0, "end": 10, "label": "a"}]}),
    )
    assert len(load_jobs(path)) == 1


def test_end_before_start_is_rejected(tmp_path: Path):
    path = _write(tmp_path, "jobs.csv", "AIYxvfxnbz8,4495,4243,Esme\n")
    with pytest.raises(BatchError, match="must be after start"):
        load_jobs(path)


def test_row_without_name_is_rejected(tmp_path: Path):
    """Nothing to name the output clip after."""
    path = _write(tmp_path, "jobs.csv", "AIYxvfxnbz8,10,20\n")
    with pytest.raises(BatchError, match="athlete or a label"):
        load_jobs(path)


def test_missing_url_is_rejected(tmp_path: Path):
    path = _write(tmp_path, "jobs.csv", ",10,20,Esme\n")
    with pytest.raises(BatchError, match="missing url"):
        load_jobs(path)


def test_bad_timecode_reports_row_number(tmp_path: Path):
    path = _write(
        tmp_path,
        "jobs.csv",
        "AIYxvfxnbz8,10,20,Esme\nAIYxvfxnbz8,nonsense,20,Esme\n",
    )
    with pytest.raises(BatchError, match="row 2"):
        load_jobs(path)


def test_empty_file(tmp_path: Path):
    assert load_jobs(_write(tmp_path, "jobs.csv", "# nothing here\n")) == []


def test_invalid_json_raises_batch_error(tmp_path: Path):
    with pytest.raises(BatchError, match="invalid JSON"):
        load_jobs(_write(tmp_path, "jobs.json", "{not json"))
