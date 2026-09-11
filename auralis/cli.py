from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import (
    PackageNotFoundError,
    version as package_version,
)
import json
import logging
import os
import shutil
import socket
import sys
from pathlib import Path

from .acquisition import (
    acquire_first_available,
    download_candidate,
)
from .discovery import search
from .enrichment import enrich
from .processing import (
    audio_info,
    convert_audio,
)
from .storage import (
    connect,
    record_observation,
)
from .trend import rank_trends

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auralis",
        description=(
            "Auralis audio discovery, acquisition "
            "and processing engine"
        ),
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    search_parser = sub.add_parser(
        "search",
        help="Discover ranked music candidates",
    )
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
    )

    trend_parser = sub.add_parser(
        "trend",
        help="Enrich YouTube candidates and rank trends",
    )
    trend_parser.add_argument("query")
    trend_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    trend_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
    )
    trend_parser.add_argument(
        "--db",
        default="data/auralis.db",
    )

    acquire_parser = sub.add_parser(
        "acquire",
        help=(
            "Discover music with Jamendo primary "
            "and YouTube fallback"
        ),
    )
    acquire_parser.add_argument("query")
    acquire_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    acquire_parser.add_argument(
        "--output-dir",
        default="assets/raw",
    )
    acquire_parser.add_argument(
        "--manifest-dir",
        default="assets/manifests",
    )

    download_parser = sub.add_parser(
        "download",
        help="Download one supported source",
    )
    download_parser.add_argument(
        "--url",
        required=True,
    )
    download_parser.add_argument(
        "--output-dir",
        default="assets/raw",
    )
    download_parser.add_argument(
        "--manifest-dir",
        default="assets/manifests",
    )
    download_parser.add_argument(
        "--cookies-file",
        default=None,
        help="YouTube cookies.txt or browser-export JSON",
    )
    download_parser.add_argument(
        "--pot-base-url",
        default=os.environ.get(
            "AURALIS_YTDLP_POT_BASE_URL"
        ),
        help="YouTube bgutil provider URL",
    )

    convert_parser = sub.add_parser(
        "convert",
        help="Convert an acquired audio asset to MP3",
    )
    convert_parser.add_argument("source")
    convert_parser.add_argument(
        "--output",
        default=None,
    )
    convert_parser.add_argument(
        "--quality",
        default="2",
    )

    music_parser = sub.add_parser(
        "get-music",
        help=(
            "Discover, acquire and convert "
            "the top music candidate"
        ),
    )
    music_parser.add_argument("query")
    music_parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    music_parser.add_argument(
        "--output-dir",
        default="assets/raw",
    )
    music_parser.add_argument(
        "--library-dir",
        default="music",
    )
    music_parser.add_argument(
        "--manifest-dir",
        default="assets/manifests",
    )
    music_parser.add_argument(
        "--cookies-file",
        default=None,
        help="YouTube cookies.txt or browser-export JSON",
    )
    music_parser.add_argument(
        "--pot-base-url",
        default=os.environ.get(
            "AURALIS_YTDLP_POT_BASE_URL"
        ),
        help="YouTube bgutil provider URL",
    )

    sub.add_parser(
        "doctor",
        help="Diagnose Auralis",
    )

    return parser


def _print_candidates(
    candidates: list,
    *,
    trend: bool = False,
) -> None:
    for index, candidate in enumerate(
        candidates,
        1,
    ):
        views = (
            candidate.view_count
            if candidate.view_count is not None
            else "?"
        )

        provider = (
            candidate.provider
            or "unknown"
        )

        if trend:
            velocity = (
                "?"
                if candidate.view_velocity_per_day
                is None
                else (
                    f"{candidate.view_velocity_per_day:,.0f}"
                    "/day"
                )
            )

            engagement = (
                "?"
                if candidate.engagement_rate
                is None
                else (
                    f"{candidate.engagement_rate * 100:.3f}%"
                )
            )

            print(
                f"{index:02d}. "
                f"[{candidate.trend_score:05.2f}] "
                f"[{provider}] "
                f"{candidate.title} | "
                f"{views} views | "
                f"velocity {velocity} | "
                f"engagement {engagement}"
            )
        else:
            print(
                f"{index:02d}. "
                f"[{candidate.score:05.2f}] "
                f"[{provider}] "
                f"{candidate.title} | "
                f"{views} views"
            )

        print(
            f"    {candidate.url}"
        )


def _version(module_name: str) -> str:
    try:
        module = __import__(module_name)
        return str(
            getattr(
                module,
                "__version__",
                "installed",
            )
        )
    except Exception:
        return "not installed"


def _package_version(
    package_name: str,
) -> str:
    try:
        return package_version(
            package_name
        )
    except PackageNotFoundError:
        return "not installed"


def _port_open(
    host: str,
    port: int,
    timeout: float = 1.5,
) -> bool:
    try:
        with socket.create_connection(
            (host, port),
            timeout=timeout,
        ):
            return True
    except OSError:
        return False


def _run_doctor() -> int:
    print("Auralis Engine Doctor")
    print("─────────────────────")

    print(
        f"Python              ✓ "
        f"{sys.version.split()[0]}"
    )

    print(
        f"yt-dlp              "
        f"{_version('yt_dlp')}"
    )

    print(
        f"FFmpeg              "
        f"{'✓' if shutil.which('ffmpeg') else '✗ not found'}"
    )

    print(
        f"Deno                "
        f"{'✓' if shutil.which('deno') else '✗ not found'}"
    )

    print(
        f"Docker              "
        f"{'✓' if shutil.which('docker') else '✗ not found'}"
    )

    print(
        f"bgutil plugin       "
        f"{_package_version('bgutil-ytdlp-pot-provider')}"
    )

    jamendo_id = os.environ.get(
        "JAMENDO_CLIENT_ID",
        "",
    ).strip()

    print(
        f"Jamendo client ID   "
        f"{'✓ configured' if jamendo_id else '✗ not configured'}"
    )

    pot_url = os.environ.get(
        "AURALIS_YTDLP_POT_BASE_URL",
        "",
    ).strip()

    if pot_url:
        print(
            f"PO provider URL     "
            f"✓ {pot_url}"
        )
    else:
        print(
            "PO provider URL     "
            "- not configured"
        )

    provider_reachable = _port_open(
        "127.0.0.1",
        4416,
    )

    print(
        "PO provider :4416    "
        f"{'✓ reachable' if provider_reachable else '⚠ not reachable'}"
    )

    cookie = os.environ.get(
        "AURALIS_YTDLP_COOKIES_FILE",
        "",
    ).strip()

    if cookie:
        cookie_path = Path(cookie).expanduser()
    else:
        cookie_path = (
            Path.cwd() / "cookies.txt"
        )

    print(
        "YouTube cookies     "
        f"{'✓ configured' if cookie_path.is_file() else '⚠ not found'}"
    )

    print("")
    print("Provider order:")
    print("  1. Jamendo")
    print("  2. YouTube / yt-dlp")

    return 0


def _write_library_manifest(
    path: Path,
    *,
    candidate,
    query: str,
    raw_path: Path,
    manifest_dir: Path,
) -> Path:
    info = audio_info(path)

    manifest_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "source_id": candidate.id,
        "source_url": candidate.url,
        "provider": candidate.provider,
        "title": candidate.title,
        "artist": (
            candidate.metadata.get("artist")
            if candidate.metadata
            else None
        ),
        "license_url": (
            candidate.metadata.get("license_url")
            if candidate.metadata
            else None
        ),
        "query": query,
        "raw_path": str(raw_path),
        "library_path": str(path),
        "format": info["format"],
        "duration": info["duration"],
        "size_bytes": info["size_bytes"],
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    manifest_path = (
        manifest_dir
        / f"{candidate.id}.library.json"
    )

    temporary = manifest_path.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary.replace(manifest_path)

    return manifest_path


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "doctor":
        return _run_doctor()

    if args.command == "search":
        candidates = search(
            args.query,
            args.limit,
        )

        payload = [
            candidate.to_dict()
            for candidate in candidates
        ]

        if args.as_json:
            print(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            _print_candidates(candidates)

        return 0

    if args.command == "trend":
        # Trend analysis remains YouTube-focused.
        candidates = search(
            args.query,
            args.limit,
        )

        youtube_candidates = [
            candidate
            for candidate in candidates
            if (
                candidate.provider
                or ""
            ).lower() == "youtube"
        ]

        enriched = [
            enrich(candidate)
            for candidate in youtube_candidates
        ]

        observed_at = datetime.now(
            timezone.utc
        ).isoformat()

        conn = connect(args.db)

        try:
            for candidate in enriched:
                record_observation(
                    conn,
                    video_id=candidate.id,
                    observed_at=observed_at,
                    view_count=candidate.view_count,
                    like_count=candidate.like_count,
                    comment_count=candidate.comment_count,
                    query=args.query,
                )

            ranked = rank_trends(
                conn,
                enriched,
            )
        finally:
            conn.close()

        if args.as_json:
            print(
                json.dumps(
                    [
                        c.to_dict()
                        for c in ranked
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            _print_candidates(
                ranked,
                trend=True,
            )

        return 0

    if args.command == "acquire":
        candidates = search(
            args.query,
            args.limit,
        )

        if not candidates:
            raise RuntimeError(
                "No music candidates found."
            )

        path = acquire_first_available(
            candidates,
            Path(args.output_dir),
            manifest_dir=Path(
                args.manifest_dir
            ),
        )

        print(path)
        return 0

    if args.command == "download":
        # Preserve explicit YouTube CLI configuration.
        if (
            "youtube.com" in args.url.lower()
            or "youtu.be" in args.url.lower()
        ):
            import auralis.acquisition.ytdlp as ytdlp

            path = ytdlp.download_audio(
                args.url,
                Path(args.output_dir),
                manifest_dir=Path(
                    args.manifest_dir
                ),
                cookies_file=args.cookies_file,
                pot_base_url=args.pot_base_url,
            )

            print(path)
            return 0

        # Native Jamendo URL support.
        if (
            "jamendo.com" in args.url.lower()
            or "jamen.do" in args.url.lower()
        ):
            from .acquisition.jamendo import (
                download_track,
            )

            match = __import__(
                "re"
            ).search(
                r"(\d+)",
                args.url,
            )

            if not match:
                raise RuntimeError(
                    "Could not determine Jamendo track ID "
                    "from URL."
                )

            track_id = match.group(1)

            from .acquisition.jamendo import (
                search_tracks,
            )

            tracks = search_tracks(
                track_id,
                limit=10,
            )

            selected = next(
                (
                    track
                    for track in tracks
                    if track.id == track_id
                ),
                None,
            )

            if selected is None:
                raise RuntimeError(
                    f"Jamendo track {track_id} "
                    "could not be resolved."
                )

            path = download_track(
                selected,
                Path(args.output_dir),
                manifest_dir=Path(
                    args.manifest_dir
                ),
            )

            print(path)
            return 0

        raise RuntimeError(
            "Unsupported source URL. "
            "Use a Jamendo or YouTube source."
        )

    if args.command == "get-music":
        candidates = search(
            args.query,
            args.limit,
        )

        if not candidates:
            raise RuntimeError(
                "No music candidates found."
            )

        raw_path = acquire_first_available(
            candidates,
            Path(args.output_dir),
            manifest_dir=Path(
                args.manifest_dir
            ),
            cookies_file=args.cookies_file,
            pot_base_url=args.pot_base_url,
        )

        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.id in raw_path.name
            ),
            candidates[0],
        )

        library_dir = Path(
            args.library_dir
        )

        library_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        final_path = convert_audio(
            raw_path,
            library_dir
            / f"{selected.id}.mp3",
            quality="2",
        )

        manifest_path = (
            _write_library_manifest(
                final_path,
                candidate=selected,
                query=args.query,
                raw_path=raw_path,
                manifest_dir=Path(
                    args.manifest_dir
                ),
            )
        )

        print(final_path)
        print(manifest_path)

        return 0

    if args.command == "convert":
        path = convert_audio(
            args.source,
            args.output,
            quality=args.quality,
        )

        print(path)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
