"""Provider-aware acquisition layer for Auralis."""

from .jamendo import (
    JamendoAcquisitionError,
    JamendoConfigurationError,
    JamendoError,
    JamendoTrack,
    candidates_from_tracks,
    download_track,
    search_candidates as search_jamendo,
    search_tracks,
)
from .router import (
    acquire_first_available,
    download_candidate,
)
from .ytdlp import (
    AudioAsset,
    ProviderCircuitOpen,
    ProviderHealth,
    download_audio as download_youtube_audio,
    write_manifest,
)

__all__ = [
    "AudioAsset",
    "ProviderCircuitOpen",
    "ProviderHealth",
    "JamendoAcquisitionError",
    "JamendoConfigurationError",
    "JamendoError",
    "JamendoTrack",
    "acquire_first_available",
    "candidates_from_tracks",
    "download_candidate",
    "download_track",
    "download_youtube_audio",
    "search_jamendo",
    "search_tracks",
    "write_manifest",
]
