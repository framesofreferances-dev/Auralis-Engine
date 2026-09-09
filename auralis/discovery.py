from __future__ import annotations

import re
from typing import Any

from yt_dlp import YoutubeDL

from .models import Candidate


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def relevance(query: str, title: str, description: str | None = None) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    haystack = _tokens(f"{title} {description or ''}")
    return len(query_tokens & haystack) / len(query_tokens)


def _popularity(view_count: int | None) -> float:
    if not view_count or view_count <= 0:
        return 0.0
    # Logarithmic scale prevents very large channels from dominating everything.
    import math

    return min(1.0, math.log10(view_count + 1) / 8.0)


def _freshness(candidate: Candidate) -> float:
    age = candidate.age_days()
    if age is None:
        return 0.0
    # Strong preference for newer candidates, with a gradual decay.
    return max(0.0, min(1.0, 1.0 - (age / 180.0)))


def score_candidate(query: str, candidate: Candidate) -> float:
    rel = relevance(query, candidate.title, candidate.description)
    pop = _popularity(candidate.view_count)
    fresh = _freshness(candidate)
    # Relevance is intentionally the largest component. Popularity alone is not trend intelligence.
    return round((rel * 0.50 + fresh * 0.25 + pop * 0.25) * 100, 2)


def search(query: str, limit: int = 10) -> list[Candidate]:
    """Search publicly discoverable YouTube results through yt-dlp.

    This function only performs discovery; acquisition is deliberately separate.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

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
        )
        candidate.score = score_candidate(query, candidate)
        candidates.append(candidate)

    return sorted(candidates, key=lambda item: item.score, reverse=True)
