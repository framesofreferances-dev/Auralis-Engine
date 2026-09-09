from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    query TEXT,
    UNIQUE(video_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_observations_video_time
    ON observations(video_id, observed_at);
"""


def connect(path: str | Path = "data/auralis.db") -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record_observation(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    observed_at: str,
    view_count: int | None,
    like_count: int | None,
    comment_count: int | None,
    query: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO observations
        (video_id, observed_at, view_count, like_count, comment_count, query)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (video_id, observed_at, view_count, like_count, comment_count, query),
    )
    conn.commit()


def recent_observations(
    conn: sqlite3.Connection, video_id: str, limit: int = 2
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM observations
            WHERE video_id = ?
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            (video_id, limit),
        )
    )


def all_observations(conn: sqlite3.Connection, video_ids: Iterable[str]) -> dict[str, list[sqlite3.Row]]:
    result: dict[str, list[sqlite3.Row]] = {}
    for video_id in video_ids:
        result[video_id] = recent_observations(conn, video_id)
    return result
