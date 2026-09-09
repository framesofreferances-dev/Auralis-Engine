"""Acquisition implementations used by Auralis."""

from .ytdlp import (
    AudioAsset,
    ProviderCircuitOpen,
    ProviderHealth,
    acquire_first_available,
    download_audio,
    write_manifest,
)

__all__ = [
    "AudioAsset",
    "ProviderCircuitOpen",
    "ProviderHealth",
    "acquire_first_available",
    "download_audio",
    "write_manifest",
]
