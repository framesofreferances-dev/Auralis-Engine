"""
Native Jamendo discovery and acquisition.

Jamendo is the PRIMARY music provider for Auralis.

This module deliberately does NOT pass Jamendo URLs through yt-dlp.
It uses Jamendo's own API and HTTP download flow directly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..models import Candidate

logger = logging.getLogger(__name__)

JAMENDO_API_BASE = "https://api.jamendo.com/v3.0"
DEFAULT_TIMEOUT = 30


class JamendoError(RuntimeError):
    """Base exception for Jamendo provider failures."""


class JamendoConfigurationError(JamendoError):
    """Raised when Jamendo is not configured correctly."""


class JamendoAcquisitionError(JamendoError):
    """Raised when a Jamendo track cannot be acquired."""


@dataclass(slots=True)
class JamendoTrack:
    """Normalized Jamendo track metadata."""

    id: str
    title: str
    artist: str | None
    duration: float | None
    license_url: str | None
    audio_url: str | None
    download_url: str | None
    download_allowed: bool
    share_url: str | None
    album: str | None = None
    releasedate: str | None = None
    image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _client_id() -> str:
    value = os.environ.get("JAMENDO_CLIENT_ID", "").strip()

    if not value:
        raise JamendoConfigurationError(
            "JAMENDO_CLIENT_ID is not configured. "
            "Create a Jamendo developer application and set JAMENDO_CLIENT_ID."
        )

    return value


def _request(
    path: str,
    *,
    params: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = f"{JAMENDO_API_BASE}/{path.lstrip('/')}"

    response = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={
            "User-Agent": "Auralis-Engine/1.0",
            "Accept": "application/json",
        },
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise JamendoError(
            f"Jamendo HTTP {response.status_code}: {response.text[:500]}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise JamendoError("Jamendo returned invalid JSON") from exc

    headers = payload.get("headers") or {}

    if headers.get("status") not in (None, "success"):
        raise JamendoError(
            str(headers.get("error_message") or "Jamendo API returned an error")
        )

    return payload


def _slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "track"


def _parse_track(item: dict[str, Any]) -> JamendoTrack:
    track_id = str(item.get("id") or "").strip()

    if not track_id:
        raise JamendoError("Jamendo returned a track without an ID")

    duration_raw = item.get("duration")

    try:
        duration = float(duration_raw) if duration_raw not in (None, "") else None
    except (TypeError, ValueError):
        duration = None

    allowed = bool(item.get("audiodownload_allowed"))

    audio_url = str(item.get("audio") or "").strip() or None
    download_url = str(item.get("audiodownload") or "").strip() or None

    # Jamendo returns audiodownload as an empty string when downloads
    # are not allowed, so never trust the URL without checking the flag.
    if not allowed:
        download_url = None

    return JamendoTrack(
        id=track_id,
        title=str(item.get("name") or f"Jamendo Track {track_id}").strip(),
        artist=str(item.get("artist_name") or "").strip() or None,
        duration=duration,
        license_url=str(item.get("license_ccurl") or "").strip() or None,
        audio_url=audio_url,
        download_url=download_url,
        download_allowed=allowed,
        share_url=str(item.get("shareurl") or item.get("shorturl") or "").strip() or None,
        album=str(item.get("album_name") or "").strip() or None,
        releasedate=str(item.get("releasedate") or "").strip() or None,
        image=str(item.get("image") or "").strip() or None,
    )


def search_tracks(
    query: str,
    *,
    limit: int = 10,
) -> list[JamendoTrack]:
    """
    Search Jamendo for music matching a natural-language query.

    Jamendo's fuzzy tag search is used because it is designed for themes,
    genres, instruments, moods, and related music tags.
    """
    if not query.strip():
        raise ValueError("query must not be empty")

    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    payload = _request(
        "tracks",
        params={
            "client_id": _client_id(),
            "format": "json",
            "limit": limit,
            "fuzzytags": query,
            "audiodlformat": "mp32",
            "audioformat": "mp32",
            "track_type": "albumtrack",
            "include": "musicinfo",
        },
    )

    results: list[JamendoTrack] = []

    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue

        try:
            results.append(_parse_track(item))
        except JamendoError as exc:
            logger.warning("Skipping malformed Jamendo track: %s", exc)

    return results


def candidates_from_tracks(
    tracks: list[JamendoTrack],
) -> list[Candidate]:
    """
    Convert Jamendo tracks into the Auralis normalized Candidate model.
    """
    candidates: list[Candidate] = []

    for track in tracks:
        candidate = Candidate(
            id=f"jamendo-{track.id}",
            title=track.title,
            url=track.share_url
            or f"https://www.jamendo.com/track/{track.id}",
            channel=track.artist,
            duration=track.duration,
            upload_date=track.releasedate,
            description=(
                f"{track.title}"
                + (f" by {track.artist}" if track.artist else "")
                + (f" | album: {track.album}" if track.album else "")
            ),
            provider="jamendo",
            metadata={
                "jamendo_id": track.id,
                "artist": track.artist,
                "album": track.album,
                "license_url": track.license_url,
                "audio_url": track.audio_url,
                "download_url": track.download_url,
                "download_allowed": track.download_allowed,
                "share_url": track.share_url,
                "image": track.image,
                "releasedate": track.releasedate,
            },
        )

        # Discovery relevance is intentionally simple here.
        candidate.score = 100.0 if track.download_allowed else 25.0
        candidates.append(candidate)

    # Downloadable tracks first.
    candidates.sort(
        key=lambda item: (
            bool(item.metadata.get("download_allowed")),
            float(item.score),
        ),
        reverse=True,
    )

    return candidates


def search_candidates(
    query: str,
    *,
    limit: int = 10,
) -> list[Candidate]:
    tracks = search_tracks(query, limit=limit)
    return candidates_from_tracks(tracks)


def _download_endpoint(track_id: str) -> str:
    return f"{JAMENDO_API_BASE}/tracks/file/"


def _resolve_download_url(track: JamendoTrack) -> str:
    """
    Resolve the actual downloadable resource.

    Preferred route:
      1. audiodownload returned by /tracks
      2. Jamendo /tracks/file redirect endpoint
    """
    if not track.download_allowed:
        raise JamendoAcquisitionError(
            f"Jamendo track {track.id} does not allow application downloads."
        )

    if track.download_url:
        return track.download_url

    response = requests.get(
        _download_endpoint(track.id),
        params={
            "client_id": _client_id(),
            "id": track.id,
            "audioformat": "mp32",
            "action": "download",
        },
        allow_redirects=False,
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": "Auralis-Engine/1.0"},
    )

    if response.status_code not in (301, 302, 303, 307, 308):
        raise JamendoAcquisitionError(
            f"Jamendo download endpoint returned HTTP {response.status_code}"
        )

    location = response.headers.get("Location")

    if not location:
        raise JamendoAcquisitionError(
            f"Jamendo download endpoint returned no redirect for track {track.id}"
        )

    return location


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _extension_from_content_type(content_type: str | None) -> str:
    value = (content_type or "").lower()

    if "mpeg" in value or "mp3" in value:
        return ".mp3"

    if "ogg" in value:
        return ".ogg"

    if "flac" in value:
        return ".flac"

    return ".mp3"


def download_track(
    track: JamendoTrack | Candidate,
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
) -> Path:
    """
    Native HTTP acquisition for Jamendo.

    This never invokes yt-dlp.
    """
    if isinstance(track, Candidate):
        metadata = track.metadata or {}

        jamendo_id = str(
            metadata.get("jamendo_id")
            or track.id.removeprefix("jamendo-")
        )

        normalized = JamendoTrack(
            id=jamendo_id,
            title=track.title,
            artist=metadata.get("artist"),
            duration=track.duration,
            license_url=metadata.get("license_url"),
            audio_url=metadata.get("audio_url"),
            download_url=metadata.get("download_url"),
            download_allowed=bool(metadata.get("download_allowed")),
            share_url=metadata.get("share_url") or track.url,
            album=metadata.get("album"),
            releasedate=metadata.get("releasedate"),
            image=metadata.get("image"),
        )

        track = normalized

    if not track.download_allowed:
        raise JamendoAcquisitionError(
            f"Jamendo track {track.id} is not marked as downloadable."
        )

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    manifest_destination = Path(manifest_dir)
    manifest_destination.mkdir(parents=True, exist_ok=True)

    download_url = _resolve_download_url(track)

    response = requests.get(
        download_url,
        stream=True,
        timeout=60,
        headers={"User-Agent": "Auralis-Engine/1.0"},
    )
    response.raise_for_status()

    extension = _extension_from_content_type(
        response.headers.get("Content-Type")
    )

    filename = (
        f"{track.id}-{_slug(track.title)}"
        f"{extension}"
    )

    output_path = destination / filename
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")

    try:
        with temporary_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

        if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
            raise JamendoAcquisitionError(
                f"Jamendo returned an empty file for track {track.id}"
            )

        temporary_path.replace(output_path)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    payload = {
        "source_id": track.id,
        "source_url": track.share_url
        or f"https://www.jamendo.com/track/{track.id}",
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "provider": "jamendo",
        "license_url": track.license_url,
        "download_allowed": track.download_allowed,
        "releasedate": track.releasedate,
        "audio_url": track.audio_url,
        "download_url": download_url,
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }

    manifest_path = manifest_destination / f"jamendo-{track.id}.json"

    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "Jamendo acquired: %s by %s -> %s",
        track.title,
        track.artist or "unknown artist",
        output_path,
    )

    return output_path
