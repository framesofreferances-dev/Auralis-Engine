"""yt-dlp based audio acquisition with supported PO-token plugin integration."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ..models import Candidate

logger = logging.getLogger(__name__)


class ProviderCircuitOpen(RuntimeError):
    """Raised when an acquisition provider circuit is open."""


class ProviderHealth:
    """Small in-process circuit breaker for acquisition providers."""

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
        self._opened_until[provider] = (
            time.monotonic() + self.cooldown_seconds
        )

        logger.warning(
            "Provider circuit opened for %s (cooldown %.0fs)",
            provider,
            self.cooldown_seconds,
        )

    def close(self, provider: str) -> None:
        self._opened_until.pop(provider, None)


_PROVIDER_HEALTH = ProviderHealth()


def _provider_for_url(url: str) -> str:
    lowered = url.lower()

    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube"

    return "generic"


def _sha256(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)

    return digest.hexdigest()


@dataclass(slots=True)
class AudioAsset:
    source_id: str
    source_url: str
    path: str
    acquired_at: str
    duration: Optional[float] = None
    size_bytes: Optional[int] = None
    format: Optional[str] = None
    sha256: Optional[str] = None
    provider: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _find_downloaded_file(
    destination: Path,
    source_id: str,
) -> Path:
    matches = sorted(
        path
        for path in destination.glob(f"{source_id}.*")
        if path.is_file()
        and not path.name.endswith(".part")
    )

    if not matches:
        raise FileNotFoundError(
            "yt-dlp reported success but no audio asset "
            f"was found for {source_id}"
        )

    return matches[0]


def _ffprobe(
    path: Path,
) -> tuple[Optional[float], Optional[str]]:
    executable = shutil.which("ffprobe")

    if not executable:
        return None, None

    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        payload = json.loads(result.stdout)
        fmt = payload.get("format", {})

        duration_raw = fmt.get("duration")

        duration = (
            float(duration_raw)
            if duration_raw not in (None, "")
            else None
        )

        format_name = fmt.get("format_name")

        return (
            duration,
            str(format_name)
            if format_name
            else None,
        )

    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:

        logger.warning(
            "ffprobe failed for %s: %s",
            path,
            exc,
        )

        return None, None


def write_manifest(
    asset: AudioAsset,
    manifest_dir: str | Path = "assets/manifests",
) -> Path:
    destination = Path(manifest_dir)

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = (
        destination
        / f"{asset.source_id}.json"
    )

    temporary = manifest_path.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            asset.to_dict(),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(manifest_path)

    return manifest_path


def _json_cookies_to_netscape(
    source: Path,
) -> Path:
    try:
        data = json.loads(
            source.read_text(
                encoding="utf-8",
            )
        )

    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:

        raise RuntimeError(
            f"Unable to read cookie JSON: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise RuntimeError(
            "Cookie JSON must contain a list "
            "of cookie objects"
        )

    cache_dir = (
        Path(tempfile.gettempdir())
        / "auralis"
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    uid = (
        os.getuid()
        if hasattr(os, "getuid")
        else "local"
    )

    converted = (
        cache_dir
        / f"youtube-cookies-{uid}.txt"
    )

    with converted.open(
        "w",
        encoding="utf-8",
    ) as handle:

        handle.write(
            "# Netscape HTTP Cookie File\n"
        )

        for cookie in data:

            if not isinstance(
                cookie,
                dict,
            ):
                continue

            domain = str(
                cookie.get(
                    "domain",
                    "",
                )
            ).strip()

            name = str(
                cookie.get(
                    "name",
                    "",
                )
            ).strip()

            if not domain or not name:
                continue

            value = str(
                cookie.get(
                    "value",
                    "",
                )
            )

            include_subdomains = (
                "TRUE"
                if domain.startswith(".")
                else "FALSE"
            )

            cookie_path = str(
                cookie.get("path")
                or "/"
            )

            secure = (
                "TRUE"
                if cookie.get(
                    "secure",
                    False,
                )
                else "FALSE"
            )

            expiration = cookie.get(
                "expirationDate",
                0,
            )

            try:
                expiration = int(
                    float(
                        expiration
                        or 0
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                expiration = 0

            handle.write(
                "\t".join(
                    [
                        domain,
                        include_subdomains,
                        cookie_path,
                        secure,
                        str(expiration),
                        name,
                        value,
                    ]
                )
                + "\n"
            )

    try:
        converted.chmod(
            stat.S_IRUSR
            | stat.S_IWUSR
        )

    except OSError:
        pass

    return converted


def _cookie_file(
    cookies_file: str | Path | None,
    *,
    base_dir: Path,
) -> Optional[Path]:

    if cookies_file is None:
        return None

    source = (
        Path(cookies_file)
        .expanduser()
    )

    if not source.is_absolute():

        cwd_source = (
            Path.cwd()
            / source
        )

        if cwd_source.exists():
            source = cwd_source

        else:
            source = (
                base_dir
                / source
            )

    if not source.exists():
        raise FileNotFoundError(
            f"Cookie file does not exist: {source}"
        )

    if source.suffix.lower() == ".json":
        return _json_cookies_to_netscape(
            source
        )

    return source


def _yt_dlp_options(
    destination: Path,
    *,
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> dict[str, Any]:

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(
            destination
            / "%(id)s.%(ext)s"
        ),
        "noplaylist": True,
        "quiet": False,
        "js_runtimes": {
            "deno": {},
        },
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 20,
    }

    resolved_pot_url = (
        pot_base_url
        or os.environ.get(
            "AURALIS_YTDLP_POT_BASE_URL",
            "",
        ).strip()
    )

    if resolved_pot_url:

        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {
                "base_url": [
                    resolved_pot_url
                ]
            }
        }

    # Priority:
    #
    # 1. Explicit cookies_file argument
    # 2. AURALIS_YTDLP_COOKIES_FILE
    # 3. ./cookies.txt
    # 4. ./youtube_cookies.json

    resolved_cookie_file = cookies_file

    if resolved_cookie_file is None:

        configured_cookie_file = (
            os.environ.get(
                "AURALIS_YTDLP_COOKIES_FILE",
                "",
            ).strip()
        )

        if configured_cookie_file:
            resolved_cookie_file = (
                configured_cookie_file
            )

    if resolved_cookie_file is not None:

        cookie_file = _cookie_file(
            resolved_cookie_file,
            base_dir=destination.parent,
        )

        if cookie_file is not None:

            options["cookiefile"] = str(
                cookie_file
            )

    else:

        for candidate in (
            Path.cwd()
            / "cookies.txt",

            Path.cwd()
            / "youtube_cookies.json",
        ):

            if candidate.exists():

                cookie_file = _cookie_file(
                    candidate,
                    base_dir=destination.parent,
                )

                if cookie_file is not None:

                    options["cookiefile"] = str(
                        cookie_file
                    )

                break

    return options


def _cached_asset(
    destination: Path,
    source_id: str,
) -> Optional[Path]:

    matches = sorted(
        path
        for path in destination.glob(
            f"{source_id}.*"
        )
        if path.is_file()
        and not path.name.endswith(".part")
    )

    return (
        matches[0]
        if matches
        else None
    )


def _validate_asset(
    path: Path,
) -> None:

    if (
        not path.exists()
        or not path.is_file()
    ):

        raise FileNotFoundError(
            f"Audio asset not found: {path}"
        )

    if path.stat().st_size <= 0:

        raise RuntimeError(
            f"Audio asset is empty: {path}"
        )


def _classify_download_error(
    exc: Exception,
) -> str:

    message = str(exc).lower()

    if (
        "sign in to confirm" in message
        or "not a bot" in message
    ):
        return (
            "provider verification required"
        )

    if (
        "cookies" in message
        and (
            "expired" in message
            or "rotated" in message
            or "invalid" in message
        )
    ):
        return (
            "youtube cookies expired or rotated"
        )

    if (
        "authentication" in message
        or "login" in message
    ):
        return (
            "authentication required"
        )

    if (
        "429" in message
        or "too many requests" in message
        or "rate limit" in message
    ):
        return "rate limited"

    if (
        "unavailable" in message
        or "private video" in message
    ):
        return "source unavailable"

    return "acquisition failed"


def download_audio(
    url: str,
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> Path:

    if not url.strip():
        raise ValueError(
            "url must not be empty"
        )

    destination = Path(output_dir)

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    provider = _provider_for_url(url)

    options = _yt_dlp_options(
        destination,
        cookies_file=cookies_file,
        pot_base_url=pot_base_url,
    )

    try:

        with YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            source_id = str(
                info.get("id")
                or "unknown"
            )

            filename = (
                _cached_asset(
                    destination,
                    source_id,
                )
                or Path(
                    ydl.prepare_filename(
                        info
                    )
                )
            )

    except DownloadError as exc:

        logger.error(
            "yt-dlp acquisition failed "
            "for %s: %s",
            url,
            exc,
            exc_info=True,
        )

        raise RuntimeError(
            f"{_classify_download_error(exc)}: {exc}"
        ) from exc

    if not filename.exists():

        filename = (
            _find_downloaded_file(
                destination,
                source_id,
            )
        )

    _validate_asset(filename)

    duration, format_name = _ffprobe(
        filename
    )

    asset = AudioAsset(
        source_id=source_id,
        source_url=url,
        path=str(filename),
        acquired_at=(
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        duration=duration,
        size_bytes=(
            filename.stat()
            .st_size
        ),
        format=format_name,
        sha256=_sha256(filename),
        provider=provider,
    )

    write_manifest(
        asset,
        manifest_dir,
    )

    return filename


def acquire_first_available(
    candidates: list[Candidate],
    output_dir: str | Path = "assets/raw",
    *,
    manifest_dir: str | Path = "assets/manifests",
    cookies_file: str | Path | None = None,
    pot_base_url: str | None = None,
) -> Path:

    failures: list[str] = []

    for candidate in candidates:

        provider = _provider_for_url(
            candidate.url
        )

        if not _PROVIDER_HEALTH.allow(
            provider
        ):

            failures.append(
                f"{candidate.id}: "
                "provider circuit open"
            )

            continue

        try:

            path = download_audio(
                candidate.url,
                output_dir,
                manifest_dir=manifest_dir,
                cookies_file=cookies_file,
                pot_base_url=pot_base_url,
            )

            _PROVIDER_HEALTH.close(
                provider
            )

            return path

        except Exception as exc:

            reason = (
                _classify_download_error(
                    exc
                )
            )

            failures.append(
                f"{candidate.id}: {reason}"
            )

            if reason in {
                "provider verification required",
                "youtube cookies expired or rotated",
                "authentication required",
                "rate limited",
            }:

                _PROVIDER_HEALTH.open(
                    provider
                )

            logger.warning(
                "Acquisition unavailable "
                "for %s: %s; trying next "
                "candidate. Details: %s",
                candidate.id,
                reason,
                exc,
            )

    detail = (
        "; ".join(failures)
        if failures
        else "no candidates supplied"
    )

    raise RuntimeError(
        "No candidate could be acquired. "
        f"{detail}"
    )
