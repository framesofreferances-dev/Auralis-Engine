from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .models import Candidate



class ProviderCircuitOpen(RuntimeError):
    pass


class ProviderHealth:
    def __init__(self, *, cooldown_seconds: float = 300.0) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._opened_until: dict[str, float] = {}

    def allow(self, provider: str) -> bool:
        until = self._opened_until.get(provider, 0.0)
        if until <= time.monotonic():
            self._opened_until.pop(provider, None)
            return True
        return False

    def open(self, provider: str) -> None:
        self._opened_until[provider] = time.monotonic() + self.cooldown_seconds

    def close(self, provider: str) -> None:
        self._opened_until.pop(provider, None)


_PROVIDER_HEALTH = ProviderHealth()


def _provider_for_url(url: str) -> str:
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"
    return "generic"


def _sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class AudioAsset:
    """A locally acquired audio asset and its source metadata."""

    source_id: str
    source_url: str
    path: str
    acquired_at: str
    duration: float | None = None
    size_bytes: int | None = None
    format: str | None = None
    sha256: str | None = None
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _find_downloaded_file(destination: Path, source_id: str) -> Path:
    matches = sorted(
        path for path in destination.glob(f"{source_id}.*")
        if path.is_file() and not path.name.endswith(".part")
    )
    if not matches:
        raise FileNotFoundError(
            f"yt-dlp reported success but no audio asset was found for {source_id}"
        )
    return matches[0]


def _ffprobe(path: Path) -> tuple[float | None, str | None]:
    executable = shutil.which("ffprobe")
    if not executable:
        return None, None

    result = subprocess.run(
        [
            executable, "-v", "error",
            "-show_entries", "format=duration,format_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    duration = float(values[0]) if values else None
    format_name = values[1] if len(values) > 1 else None
    return duration, format_name


def write_manifest(asset: AudioAsset, manifest_dir: str | Path = "assets/manifests") -> Path:
    """Persist acquisition metadata without storing secrets or cookies."""
    destination = Path(manifest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / f"{asset.source_id}.json"
    payload = json.dumps(asset.to_dict(), indent=2, ensure_ascii=False) + "\n"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest_path


def _cookie_file(explicit: str | Path | None = None, *, base_dir: str | Path | None = None) -> Path | None:
    """Resolve a local yt-dlp cookie source.

    Supported inputs:
      * Netscape-format cookies.txt
      * browser-extension JSON cookie exports

    JSON exports are converted to a private temporary Netscape file because
    yt-dlp expects Netscape format for --cookies. Cookie values are never
    printed, manifested, or committed by Auralis.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    configured = os.environ.get("AURALIS_YTDLP_COOKIES_FILE", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    cwd = Path.cwd()
    if base_dir is not None:
        local = Path(base_dir).expanduser()
        candidates.extend([local / "cookies.txt", local / "youtube_cookies.json"])
    candidates.extend([cwd / "cookies.txt", cwd / "youtube_cookies.json"])

    source = next((path.resolve() for path in candidates if path.is_file()), None)
    if source is None:
        return None

    if source.suffix.lower() != ".json":
        return source

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read cookie JSON: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError("Cookie JSON must contain a list of cookie objects")

    cache_dir = Path(tempfile.gettempdir()) / "auralis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    converted = cache_dir / f"youtube-cookies-{os.getuid() if hasattr(os, 'getuid') else 'local'}.txt"

    with converted.open("w", encoding="utf-8") as handle:
        handle.write("# Netscape HTTP Cookie File\n")
        for cookie in data:
            if not isinstance(cookie, dict):
                continue
            domain = str(cookie.get("domain", "")).strip()
            name = str(cookie.get("name", "")).strip()
            value = str(cookie.get("value", ""))
            if not domain or not name:
                continue

            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = str(cookie.get("path") or "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expiration = cookie.get("expirationDate", 0)
            try:
                expiration = int(float(expiration or 0))
            except (TypeError, ValueError):
                expiration = 0

            # Netscape cookie files use TAB-separated fields.
            handle.write(
                "\t".join(
                    [
                        domain,
                        include_subdomains,
                        path,
                        secure,
                        str(expiration),
                        name,
                        value,
                    ]
                )
                + "\n"
            )

    try:
        converted.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return converted


def _yt_dlp_options(
    destination: Path,
    *,
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> dict[str, Any]:
    """Build acquisition options for public or explicitly authorized sources."""
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(destination / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "js_runtimes": {"deno": {}},
    }
    resolved_pot_url = (
        pot_base_url
        or os.environ.get("AURALIS_YTDLP_POT_BASE_URL", "").strip()
    )
    if resolved_pot_url:
        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {"base_url": [resolved_pot_url]}
        }

    cookie_file = _cookie_file(cookies_file, base_dir=destination.parent)
    if cookie_file is not None:
        options["cookiefile"] = str(cookie_file)
    return options


def _classify_download_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "sign in to confirm" in message or "not a bot" in message:
        return "provider verification required"
    if "video unavailable" in message or "private video" in message:
        return "source unavailable"
    if "provided youtube account cookies are no longer valid" in message or "cookies are no longer valid" in message:
        return "youtube cookies expired or rotated"
    if "login" in message or "authentication" in message:
        return "authentication required"
    if "drm" in message:
        return "drm protected source"
    return "provider acquisition failed"



def _cached_asset(destination: Path, source_id: str) -> Path | None:
    try:
        return _find_downloaded_file(destination, source_id)
    except FileNotFoundError:
        return None


def _validate_asset(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Downloaded asset does not exist: {path}")
    if path.stat().st_size < 1024:
        raise RuntimeError(f"Downloaded asset is suspiciously small: {path}")


def acquire_first_available(
    candidates: list[Candidate],
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> Path:
    """Try ranked candidates in order without bypassing provider controls.\n\n    Provider failures are classified so the caller gets an actionable reason.\n    A circuit prevents hammering a provider after a hard verification/auth failure.\n    """
    failures: list[str] = []
    for candidate in candidates:
        provider = _provider_for_url(candidate.url)
        if not _PROVIDER_HEALTH.allow(provider):
            failures.append(f"{candidate.id}: provider circuit open")
            print(f"Acquisition skipped for {candidate.id}: {provider} circuit open; trying next candidate.")
            continue
        try:
            path = download_audio(
                candidate.url,
                output_dir,
                manifest_dir=manifest_dir,
                cookies_file=cookies_file,
                pot_base_url=pot_base_url,
            )
            _PROVIDER_HEALTH.close(provider)
            return path
        except Exception as exc:
            reason = _classify_download_error(exc)
            failures.append(f"{candidate.id}: {reason}")
            if reason in {"provider verification required", "youtube cookies expired or rotated", "authentication required", "drm protected source"}:
                _PROVIDER_HEALTH.open(provider)
            print(
                f"Acquisition unavailable for {candidate.id}: "
                f"{reason}; trying next candidate."
            )
    detail = "; ".join(failures) if failures else "no candidates supplied"
    raise RuntimeError(f"No candidate could be acquired. {detail}")


def download_audio(
    url: str,
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> Path:
    """Acquire one permitted audio source through yt-dlp.

    Auralis intentionally does not bypass DRM, paywalls, or provider security
    controls. Cookies are supported only when supplied by the user for an
    account/source they are authorized to access.
    """
    if not url.strip():
        raise ValueError("url must not be empty")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    provider = _provider_for_url(url)
    options = _yt_dlp_options(
        destination,
        cookies_file=cookies_file,
        pot_base_url=pot_base_url,
    )

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            source_id = str(info.get("id") or "unknown")
            filename = _cached_asset(destination, source_id) or Path(ydl.prepare_filename(info))
    except DownloadError as exc:
        raise RuntimeError(f"{_classify_download_error(exc)}: {exc}") from exc

    if not filename.exists():
        filename = _find_downloaded_file(destination, source_id)

    _validate_asset(filename)
    digest = _sha256(filename)
    duration, format_name = _ffprobe(filename)
    asset = AudioAsset(
        source_id=source_id,
        source_url=url,
        path=str(filename),
        acquired_at=datetime.now(timezone.utc).isoformat(),
        duration=duration,
        size_bytes=filename.stat().st_size,
        format=format_name,
        sha256=digest,
        provider=provider,
    )
    write_manifest(asset, manifest_dir)
    return filename
