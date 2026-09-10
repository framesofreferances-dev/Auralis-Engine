from __future__ import annotations

import logging
import math
import re
from typing import Any

from yt_dlp import YoutubeDL

from .acquisition.jamendo import (
    JamendoConfigurationError,
    JamendoError,
    search_candidates as search_jamendo,
)
from .models import Candidate

logger = logging.getLogger(__name__)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2
    }


def relevance(
    query: str,
    title: str,
    description: str | None = None,
) -> float:
    query_tokens = _tokens(query)

    if not query_tokens:
        return 0.0

    haystack = _tokens(
        f"{title} {description or ''}"
    )

    return len(query_tokens & haystack) / len(query_tokens)


def _popularity(view_count: int | None) -> float:
    if not view_count or view_count <= 0:
        return 0.0

    return min(
        1.0,
        math.log10(view_count + 1) / 8.0,
    )


def _freshness(candidate: Candidate) -> float:
    age = candidate.age_days()

    if age is None:
        return 0.0

    return max(
        0.0,
        min(1.0, 1.0 - (age / 180.0)),
    )


def score_candidate(
    query: str,
    candidate: Candidate,
) -> float:
    rel = relevance(
        query,
        candidate.title,
        candidate.description,
    )

    pop = _popularity(candidate.view_count)
    fresh = _freshness(candidate)

    provider_bonus = {
        "jamendo": 10.0,
        "youtube": 0.0,
    }.get(
        (candidate.provider or "").lower(),
        0.0,
    )

    score = (
        rel * 50.0
        + fresh * 25.0
        + pop * 25.0
        + provider_bonus
    )

    return round(score, 2)


def _search_youtube(
    query: str,
    limit: int,
) -> list[Candidate]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            f"ytsearch{limit}:{query}",
            download=False,
        )

    candidates: list[Candidate] = []

    for item in info.get("entries", []) if info else []:
        if not item:
            continue

        candidate = Candidate(
            id=str(item.get("id", "")),
            title=str(
                item.get("title")
                or "Untitled"
            ),
            url=str(
                item.get("webpage_url")
                or item.get("url")
                or ""
            ),
            channel=(
                item.get("channel")
                or item.get("uploader")
            ),
            duration=item.get("duration"),
            view_count=item.get("view_count"),
            upload_date=item.get("upload_date"),
            description=item.get("description"),
            provider="youtube",
            metadata={
                "youtube_id": str(
                    item.get("id", "")
                ),
            },
        )

        candidate.score = score_candidate(
            query,
            candidate,
        )

        candidates.append(candidate)

    return sorted(
        candidates,
        key=lambda item: item.score,
        reverse=True,
    )


def search(
    query: str,
    limit: int = 10,
) -> list[Candidate]:
    """
    Music discovery.

    Priority:
        1. Jamendo PRIMARY
        2. YouTube SECONDARY

    Both candidate groups are retained so that the acquisition
    layer has a genuine fallback path.
    """
    if not query.strip():
        raise ValueError(
            "query must not be empty"
        )

    if not 1 <= limit <= 50:
        raise ValueError(
            "limit must be between 1 and 50"
        )

    jamendo_candidates: list[Candidate] = []

    try:
        jamendo_candidates = search_jamendo(
            query,
            limit=limit,
        )

        for candidate in jamendo_candidates:
            candidate.score = score_candidate(
                query,
                candidate,
            )

        logger.info(
            "Jamendo returned %d candidates",
            len(jamendo_candidates),
        )

    except JamendoConfigurationError as exc:
        logger.warning(
            "Jamendo is not configured: %s",
            exc,
        )

    except JamendoError as exc:
        logger.warning(
            "Jamendo discovery failed: %s",
            exc,
        )

    youtube_candidates: list[Candidate] = []

    # Keep YouTube candidates available as secondary
    # acquisition fallback.
    try:
        youtube_candidates = _search_youtube(
            query,
            limit,
        )
    except Exception as exc:
        logger.warning(
            "YouTube discovery failed: %s",
            exc,
        )

    combined = (
        jamendo_candidates
        + youtube_candidates
    )

    if not combined:
        return []

    # Never let YouTube jump ahead of Jamendo merely because
    # YouTube has views/popularity.
    provider_priority = {
        "jamendo": 0,
        "youtube": 1,
    }

    return sorted(
        combined,
        key=lambda candidate: (
            provider_priority.get(
                (candidate.provider or "").lower(),
                99,
            ),
            -candidate.score,
        ),
    )
