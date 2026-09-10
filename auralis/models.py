from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Candidate:
    """A normalized music/video discovery result."""

    id: str
    title: str
    url: str

    channel: str | None = None
    duration: float | None = None
    view_count: int | None = None
    upload_date: str | None = None
    description: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    timestamp: float | None = None

    score: float = 0.0
    trend_score: float = 0.0
    view_velocity_per_day: float | None = None
    engagement_rate: float | None = None

    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def age_days(self) -> float | None:
        if self.timestamp is not None:
            return max(
                0.0,
                (
                    datetime.now(timezone.utc).timestamp()
                    - self.timestamp
                )
                / 86400,
            )

        if not self.upload_date:
            return None

        try:
            published = datetime.strptime(
                self.upload_date,
                "%Y%m%d",
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        return max(
            0.0,
            (
                datetime.now(timezone.utc) - published
            ).total_seconds()
            / 86400,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
