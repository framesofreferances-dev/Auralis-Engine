from __future__ import annotations

import logging
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .models import Candidate

logger = logging.getLogger(__name__)


class _SilentYTDLPLogger:
    """Keep yt-dlp provider errors out of the CLI when enrichment is optional."""

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def enrich(candidate: Candidate) -> Candidate:
    """Best-effort metadata enrichment without downloading media.

    Discovery metadata remains authoritative enough for ranking, so an individual
    YouTube extraction failure must never discard the candidate or abort a trend scan.
    When enrichment succeeds, richer fields are merged into the existing candidate.
    Provider/library errors are intentionally reduced to one concise Auralis warning.
    """
    if not candidate.url:
        return candidate

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "logger": _SilentYTDLPLogger(),
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(candidate.url, download=False)
    except DownloadError as exc:
        logger.warning(
            "Enrichment unavailable for %s: YouTube/provider verification required; using discovery metadata.",
            candidate.id,
        )
        return candidate
    except Exception as exc:  # defensive boundary for provider/library failures
        logger.warning(
            "Enrichment unavailable for %s: %s; using discovery metadata.",
            candidate.id,
            exc,
        )
        return candidate

    if not info:
        return candidate

    candidate.channel = info.get("channel") or info.get("uploader") or candidate.channel
    candidate.duration = info.get("duration") if info.get("duration") is not None else candidate.duration
    candidate.view_count = info.get("view_count") if info.get("view_count") is not None else candidate.view_count
    candidate.like_count = info.get("like_count")
    candidate.comment_count = info.get("comment_count")
    candidate.upload_date = info.get("upload_date") or candidate.upload_date
    candidate.timestamp = info.get("timestamp")
    candidate.description = info.get("description") or candidate.description
    return candidate
