from __future__ import annotations

import logging
import os
from pathlib import Path

from ..models import Candidate
from .jamendo import (
    JamendoAcquisitionError,
    JamendoError,
    download_track,
)
from .ytdlp import (
    acquire_first_available as acquire_youtube_candidates,
    download_audio as download_youtube_audio,
)

logger = logging.getLogger(__name__)


def _provider_for_candidate(candidate: Candidate) -> str:
    if candidate.provider:
        return candidate.provider.lower()

    url = candidate.url.lower()

    if "jamendo.com" in url or "jamen.do" in url:
        return "jamendo"

    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"

    return "generic"


def _youtube_kwargs() -> dict:
    cookies_file = os.environ.get(
        "AURALIS_YTDLP_COOKIES_FILE",
        "",
    ).strip()

    pot_base_url = os.environ.get(
        "AURALIS_YTDLP_POT_BASE_URL",
        "",
    ).strip()

    return {
        "cookies_file": cookies_file or None,
        "pot_base_url": pot_base_url or None,
    }


def download_candidate(
    candidate: Candidate,
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
) -> Path:
    """
    Acquire one candidate using the provider-native implementation.
    """
    provider = _provider_for_candidate(candidate)

    if provider == "jamendo":
        return download_track(
            candidate,
            output_dir=output_dir,
            manifest_dir=manifest_dir,
        )

    if provider == "youtube":
        kwargs = _youtube_kwargs()

        return download_youtube_audio(
            candidate.url,
            output_dir,
            manifest_dir=manifest_dir,
            **kwargs,
        )

    raise RuntimeError(
        f"Unsupported music provider: {provider}"
    )


def acquire_first_available(
    candidates: list[Candidate],
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
) -> Path:
    """
    Provider-aware acquisition with deterministic priority.

    Candidate order supplied by discovery is preserved:
        Jamendo PRIMARY
        YouTube SECONDARY
    """
    failures: list[str] = []

    for candidate in candidates:
        provider = _provider_for_candidate(candidate)

        try:
            print(
                f"🎵 Acquisition candidate: "
                f"{candidate.title} "
                f"[provider={provider}]"
            )

            path = download_candidate(
                candidate,
                output_dir,
                manifest_dir=manifest_dir,
            )

            print(
                f"✅ Music acquired via {provider}: {path}"
            )

            return path

        except (
            JamendoError,
            JamendoAcquisitionError,
            FileNotFoundError,
            RuntimeError,
            OSError,
        ) as exc:
            reason = str(exc)

            failures.append(
                f"{candidate.id} [{provider}]: {reason}"
            )

            logger.warning(
                "Provider acquisition failed: %s",
                failures[-1],
            )

            print(
                f"⚠️ Acquisition failed via {provider}: "
                f"{reason}"
            )

    detail = "; ".join(failures)

    raise RuntimeError(
        "No candidate could be acquired. "
        f"{detail}"
    )
