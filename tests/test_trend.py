import sqlite3

from auralis.models import Candidate
from auralis.storage import connect, record_observation
from auralis.trend import score_trend


def test_trend_score_is_bounded() -> None:
    candidate = Candidate(
        id="x",
        title="cinematic ambient",
        url="https://example.com/x",
        view_count=100_000,
        like_count=2_000,
        comment_count=100,
        upload_date="20260825",
        score=70.0,
    )
    scored = score_trend(candidate, [])
    assert 0 <= scored.trend_score <= 100


def test_observed_velocity_uses_two_snapshots(tmp_path) -> None:
    db = tmp_path / "test.db"
    conn = connect(db)
    try:
        record_observation(
            conn,
            video_id="x",
            observed_at="2026-08-25T12:00:00+00:00",
            view_count=100_000,
            like_count=1_000,
            comment_count=50,
        )
        record_observation(
            conn,
            video_id="x",
            observed_at="2026-08-26T12:00:00+00:00",
            view_count=112_000,
            like_count=1_200,
            comment_count=60,
        )
        rows = list(
            conn.execute(
                "SELECT * FROM observations WHERE video_id = 'x' ORDER BY observed_at DESC"
            )
        )
        candidate = Candidate(
            id="x",
            title="x",
            url="https://example.com/x",
            view_count=112_000,
            score=50.0,
        )
        scored = score_trend(candidate, rows)
        assert scored.view_velocity_per_day == 12_000
    finally:
        conn.close()
