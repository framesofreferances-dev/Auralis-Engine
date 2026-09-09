from __future__ import annotations

import math
from datetime import datetime, timezone
import sqlite3

from .models import Candidate
from .storage import recent_observations


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _engagement_rate(candidate: Candidate) -> float:
    views = candidate.view_count or 0
    if views <= 0:
        return 0.0
    interactions = (candidate.like_count or 0) + (candidate.comment_count or 0)
    return interactions / views


def _velocity(candidate: Candidate, observations: list[sqlite3.Row]) -> float | None:
    """Return observed views/day when two historical snapshots exist.

    With only one snapshot, return a clearly-labelled age-adjusted proxy so the
    engine can still rank candidates. Once Auralis has repeated observations,
    the measured velocity automatically replaces this proxy.
    """
    if len(observations) >= 2:
        newer, older = observations[0], observations[1]
        try:
            t_new = datetime.fromisoformat(newer["observed_at"])
            t_old = datetime.fromisoformat(older["observed_at"])
            days = max((t_new - t_old).total_seconds() / 86400, 1 / 1440)
            delta = (newer["view_count"] or 0) - (older["view_count"] or 0)
            return max(0.0, delta / days)
        except (TypeError, ValueError):
            pass

    age = candidate.age_days()
    if age is None or age < 1 / 1440 or not candidate.view_count:
        return None
    return max(0.0, candidate.view_count / age)


def score_trend(candidate: Candidate, observations: list[sqlite3.Row]) -> Candidate:
    age = candidate.age_days()
    recency = 0.0 if age is None else math.exp(-age / 30.0)

    velocity = _velocity(candidate, observations)
    candidate.view_velocity_per_day = velocity

    # Log scaling prevents a single giant channel from swallowing the ranking.
    velocity_signal = 0.0 if velocity is None else _clamp(math.log10(velocity + 1) / 6.0)

    engagement = _engagement_rate(candidate)
    candidate.engagement_rate = engagement
    engagement_signal = _clamp(math.log10(engagement * 100000 + 1) / 5.0)

    # Existing discovery score remains part of the decision; trend intelligence
    # should improve ranking rather than silently replace relevance.
    discovery_signal = _clamp(candidate.score / 100.0)

    candidate.trend_score = round(
        (
            discovery_signal * 0.35
            + recency * 0.20
            + velocity_signal * 0.35
            + engagement_signal * 0.10
        )
        * 100,
        2,
    )
    return candidate


def rank_trends(conn: sqlite3.Connection, candidates: list[Candidate]) -> list[Candidate]:
    for candidate in candidates:
        score_trend(candidate, recent_observations(conn, candidate.id))
    return sorted(candidates, key=lambda item: item.trend_score, reverse=True)
