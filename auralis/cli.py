from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .acquisition import acquire_first_available, download_audio
from .discovery import search
from .enrichment import enrich
from .storage import connect, record_observation
from .trend import rank_trends
from .processing import audio_info, convert_audio

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auralis",
        description="Auralis audio discovery and acquisition engine",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    search_parser = sub.add_parser("search", help="Discover and rank audio candidates")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true", dest="as_json")

    trend_parser = sub.add_parser("trend", help="Enrich candidates and rank trend signals")
    trend_parser.add_argument("query")
    trend_parser.add_argument("--limit", type=int, default=10)
    trend_parser.add_argument("--json", action="store_true", dest="as_json")
    trend_parser.add_argument("--db", default="data/auralis.db")

    for name, help_text in (
        ("download", "Acquire one selected audio source"),
        ("acquire", "Discover candidates and acquire the first available source"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name == "download":
            p.add_argument("--url", required=True)
        else:
            p.add_argument("query")
            p.add_argument("--limit", type=int, default=10)
        p.add_argument("--output-dir", default="assets/raw")
        p.add_argument("--manifest-dir", default="assets/manifests")
        p.add_argument("--cookies-file", default=None, help="yt-dlp cookies.txt or browser-export JSON")
        p.add_argument(
            "--pot-base-url",
            default=os.environ.get("AURALIS_YTDLP_POT_BASE_URL"),
            help="bgutil provider URL (default: http://127.0.0.1:4416 when provider is installed/configured)",
        )

    convert_parser = sub.add_parser("convert", help="Convert an acquired audio asset to MP3")
    convert_parser.add_argument("source")
    convert_parser.add_argument("--output", default=None)
    convert_parser.add_argument("--quality", default="2", help="FFmpeg VBR quality 0-9; 2 is high quality")

    music_parser = sub.add_parser("get-music", help="Discover, acquire and convert the top music candidate")
    music_parser.add_argument("query")
    music_parser.add_argument("--limit", type=int, default=10)
    music_parser.add_argument("--output-dir", default="assets/raw")
    music_parser.add_argument("--library-dir", default="music")
    music_parser.add_argument("--manifest-dir", default="assets/manifests")
    music_parser.add_argument("--cookies-file", default=None, help="yt-dlp cookies.txt or browser-export JSON")
    music_parser.add_argument(
        "--pot-base-url",
        default=os.environ.get("AURALIS_YTDLP_POT_BASE_URL"),
        help="bgutil provider URL",
    )

    sub.add_parser("doctor", help="Diagnose Auralis acquisition dependencies and provider configuration")
    return parser


def _print_candidates(candidates: list, *, trend: bool = False) -> None:
    for index, candidate in enumerate(candidates, 1):
        views = candidate.view_count if candidate.view_count is not None else "?"
        if trend:
            velocity = "?" if candidate.view_velocity_per_day is None else f"{candidate.view_velocity_per_day:,.0f}/day"
            engagement = "?" if candidate.engagement_rate is None else f"{candidate.engagement_rate * 100:.3f}%"
            print(f"{index:02d}. [{candidate.trend_score:05.2f}] {candidate.title} | {views} views | velocity {velocity} | engagement {engagement}")
        else:
            print(f"{index:02d}. [{candidate.score:05.2f}] {candidate.title} | {views} views")
        print(f"    {candidate.url}")


def _version(module_name: str) -> str:
    try:
        module = __import__(module_name)
        return str(getattr(module, "__version__", "installed"))
    except Exception:
        return "not installed"


def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_doctor() -> int:
    print("Auralis Engine Doctor")
    print("─────────────────────")
    print(f"Python              ✓ {sys.version.split()[0]}")
    print(f"yt-dlp              ✓ {_version('yt_dlp')}")
    print(f"FFmpeg              {'✓' if shutil.which('ffmpeg') else '✗ not found'}")
    print(f"Deno                {'✓' if shutil.which('deno') else '✗ not found'}")
    print(f"Docker              {'✓' if shutil.which('docker') else '✗ not found'}")

    try:
        bgutil_version = package_version("bgutil-ytdlp-pot-provider")
        print(f"bgutil plugin        ✓ {bgutil_version}")
    except PackageNotFoundError:
        print("bgutil plugin        ✗ not installed")

    pot_url = os.environ.get("AURALIS_YTDLP_POT_BASE_URL", "").strip()
    if pot_url:
        print(f"PO provider URL      ✓ {pot_url}")
    else:
        print("PO provider URL      - not configured (default plugin endpoint may be used)")

    provider_reachable = _port_open("127.0.0.1", 4416)
    print(f"PO provider :4416     {'✓ reachable' if provider_reachable else '⚠ not reachable'}")

    cookie = os.environ.get("AURALIS_YTDLP_COOKIES_FILE", "").strip()
    if cookie:
        cookie_path = Path(cookie).expanduser()
    else:
        cookie_path = Path.cwd() / "cookies.txt"
        if not cookie_path.exists():
            cookie_path = Path.cwd() / "youtube_cookies.json"
    print(f"Cookie source        {'✓ ' + cookie_path.name if cookie_path.exists() else '⚠ not found'}")
    print("")
    print("Interpretation:")
    print("- Cookies and PO tokens solve different layers of YouTube access.")
    print("- A cookie warning means the session itself was rejected; a PO provider does not refresh cookies.")
    print("- A missing provider means yt-dlp cannot obtain provider-generated PO tokens when a client requires them.")
    print("- A working provider does not guarantee that every YouTube request will succeed.")
    return 0


def _write_library_manifest(
    path: Path,
    *,
    candidate,
    query: str,
    raw_path: Path,
    manifest_dir: Path,
) -> Path:
    """Write stable metadata for the final library asset without storing secrets."""
    info = audio_info(path)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_id": candidate.id,
        "source_url": candidate.url,
        "title": candidate.title,
        "query": query,
        "raw_path": str(raw_path),
        "library_path": str(path),
        "format": info["format"],
        "duration": info["duration"],
        "size_bytes": info["size_bytes"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = manifest_dir / f"{candidate.id}.library.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest_path


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "convert":
        path = convert_audio(args.source, args.output, quality=args.quality)
        print(path)
        return 0

    if args.command == "get-music":
        candidates = search(args.query, args.limit)
        if not candidates:
            raise RuntimeError("No music candidates found")
        raw_path = acquire_first_available(
            candidates,
            Path(args.output_dir),
            manifest_dir=Path(args.manifest_dir),
            cookies_file=args.cookies_file,
            pot_base_url=args.pot_base_url,
        )
        selected = next((candidate for candidate in candidates if candidate.id == raw_path.stem), candidates[0])
        library_dir = Path(args.library_dir)
        library_dir.mkdir(parents=True, exist_ok=True)
        final_path = convert_audio(
            raw_path,
            library_dir / f"{selected.id}.mp3",
            quality="2",
        )
        manifest_path = _write_library_manifest(
            final_path,
            candidate=selected,
            query=args.query,
            raw_path=raw_path,
            manifest_dir=Path(args.manifest_dir),
        )
        print(final_path)
        print(manifest_path)
        return 0

    if args.command == "doctor":
        return _run_doctor()

    if args.command == "search":
        candidates = search(args.query, args.limit)
        payload = [candidate.to_dict() for candidate in candidates]
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.as_json else "")
        if not args.as_json:
            _print_candidates(candidates)
        return 0

    if args.command == "trend":
        candidates = search(args.query, args.limit)
        enriched = [enrich(candidate) for candidate in candidates]
        observed_at = datetime.now(timezone.utc).isoformat()
        conn = connect(args.db)
        try:
            for candidate in enriched:
                record_observation(
                    conn, video_id=candidate.id, observed_at=observed_at,
                    view_count=candidate.view_count, like_count=candidate.like_count,
                    comment_count=candidate.comment_count, query=args.query,
                )
            ranked = rank_trends(conn, enriched)
        finally:
            conn.close()
        if args.as_json:
            print(json.dumps([c.to_dict() for c in ranked], indent=2, ensure_ascii=False))
        else:
            _print_candidates(ranked, trend=True)
        return 0

    if args.command == "download":
        path = download_audio(
            args.url, Path(args.output_dir),
            manifest_dir=Path(args.manifest_dir),
            cookies_file=args.cookies_file,
            pot_base_url=args.pot_base_url,
        )
        print(path)
        return 0

    if args.command == "acquire":
        candidates = search(args.query, args.limit)
        path = acquire_first_available(
            candidates, Path(args.output_dir),
            manifest_dir=Path(args.manifest_dir),
            cookies_file=args.cookies_file,
            pot_base_url=args.pot_base_url,
        )
        print(path)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
