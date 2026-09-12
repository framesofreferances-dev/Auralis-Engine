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

    haystack = _tokens(f"{title} {description or ''}")
    return len(query_tokens & haystack) / len(query_tokens)


def _popularity(view_count: int | None) -> float:
    if not view_count or view_count <= 0:
        return 0.0

    return min(1.0, math.log10(view_count + 1) / 8.0)


def _freshness(candidate: Candidate) -> float:
    age = candidate.age_days()
    if age is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (age / 180.0)))


def score_candidate(query: str, candidate: Candidate) -> float:
    rel = relevance(query, candidate.title, candidate.description)
    pop = _popularity(candidate.view_count)
    fresh = _freshness(candidate)

    provider_bonus = {
        "jamendo": 10.0,
        "youtube": 0.0,
    }.get((candidate.provider or "").lower(), 0.0)

    return round(
        rel * 50.0
        + fresh * 25.0
        + pop * 25.0
        + provider_bonus,
        2,
    )


def _search_youtube(query: str, limit: int) -> list[Candidate]:
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
            title=str(item.get("title") or "Untitled"),
            url=str(item.get("webpage_url") or item.get("url") or ""),
            channel=item.get("channel") or item.get("uploader"),
            duration=item.get("duration"),
            view_count=item.get("view_count"),
            upload_date=item.get("upload_date"),
            description=item.get("description"),
            provider="youtube",
            metadata={"youtube_id": str(item.get("id", ""))},
        )
        candidate.score = score_candidate(query, candidate)
        candidates.append(candidate)

    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _jamendo_queries(query: str) -> list[str]:
    """Return progressively broader Jamendo tag queries.

    Groq intentionally produces rich production-language prompts. Jamendo's
    fuzzy tag search works better with a compact set of genre/mood/instrument
    terms, so keep the original query first and progressively simplify it when
    the provider returns no candidates.
    """
    tokens = _tokens(query)
    preferred = [
        "cinematic",
        "ambient",
        "instrumental",
        "eerie",
        "ethereal",
        "atmospheric",
        "drone",
        "synth",
        "electronic",
        "dark",
        "glitch",
        "metallic",
        "brass",
        "foggy",
        "calm",
    ]

    compact = [word for word in preferred if word in tokens]
    queries = [query.strip()]

    if compact:
        queries.append(" ".join(compact[:4]))
        queries.append(" ".join(compact[:2]))

    queries.extend(["cinematic ambient instrumental", "ambient instrumental", "instrumental"])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in queries:
        normalized = candidate.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return unique


def _search_jamendo_resilient(query: str, limit: int) -> list[Candidate]:
    last_error: Exception | None = None

    for attempt, jamendo_query in enumerate(_jamendo_queries(query), start=1):
        try:
            candidates = search_jamendo(jamendo_query, limit=limit)
            for candidate in candidates:
                candidate.score = score_candidate(query, candidate)

            if candidates:
                if attempt > 1:
                    logger.info(
                        "Jamendo fallback query succeeded: %s",
                        jamendo_query,
                    )
                return candidates

            logger.info(
                "Jamendo returned no candidates for query: %s",
                jamendo_query,
            )
        except (JamendoConfigurationError, JamendoError) as exc:
            last_error = exc
            if isinstance(exc, JamendoConfigurationError):
                raise
            logger.warning(
                "Jamendo query failed (%s): %s",
                jamendo_query,
                exc,
            )

    if last_error:
        raise last_error
    return []


def search(query: str, limit: int = 10) -> list[Candidate]:
    """Discover music with Jamendo primary and YouTube secondary fallback."""
    if not query.strip():
        raise ValueError("query must not be empty")

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    jamendo_candidates: list[Candidate] = []

    try:
        jamendo_candidates = _search_jamendo_resilient(query, limit)
        logger.info("Jamendo returned %d candidates", len(jamendo_candidates))
    except JamendoConfigurationError as exc:
        logger.warning("Jamendo is not configured: %s", exc)
    except JamendoError as exc:
        logger.warning("Jamendo discovery failed: %s", exc)

    youtube_candidates: list[Candidate] = []

    try:
        youtube_candidates = _search_youtube(query, limit)
    except Exception as exc:
        logger.warning("YouTube discovery failed: %s", exc)

    combined = jamendo_candidates + youtube_candidates
    if not combined:
        return []

    provider_priority = {"jamendo": 0, "youtube": 1}

    return sorted(
        combined,
        key=lambda candidate: (
            provider_priority.get((candidate.provider or "").lower(), 99),
            -candidate.score,
        ),
    )
