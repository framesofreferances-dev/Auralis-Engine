"""Acquisition implementations used by Auralis."""

from .ytdlp import (
    AudioAsset,
    ProviderCircuitOpen,
    ProviderHealth,
    _classify_download_error,
    _find_downloaded_file,
    _sha256,
    _yt_dlp_options,
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
    "_classify_download_error",
    "_find_downloaded_file",
    "_sha256",
    "_yt_dlp_options",
]
