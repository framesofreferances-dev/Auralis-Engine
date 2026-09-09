import json
from pathlib import Path

from auralis.acquisition import AudioAsset, _find_downloaded_file, write_manifest


def test_find_downloaded_file_ignores_partial_files(tmp_path: Path) -> None:
    (tmp_path / "abc.webm.part").write_bytes(b"partial")
    expected = tmp_path / "abc.webm"
    expected.write_bytes(b"audio")
    assert _find_downloaded_file(tmp_path, "abc") == expected


def test_write_manifest_persists_metadata(tmp_path: Path) -> None:
    asset = AudioAsset(
        source_id="abc",
        source_url="https://example.com/audio",
        path="assets/raw/abc.webm",
        acquired_at="2026-08-26T00:00:00+00:00",
        duration=12.5,
        size_bytes=1234,
        format="webm",
    )
    manifest = write_manifest(asset, tmp_path)
    text = manifest.read_text(encoding="utf-8")
    assert manifest.name == "abc.json"
    assert '"source_id": "abc"' in text
    assert '"duration": 12.5' in text


def test_yt_dlp_options_uses_explicit_cookie_file(tmp_path):
    from auralis.acquisition import _yt_dlp_options

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    options = _yt_dlp_options(tmp_path / "raw", cookies_file=cookie_file)

    assert options["cookiefile"] == str(cookie_file)
    assert options["js_runtimes"] == {"deno": {}}
    assert "po_token" not in options


def test_yt_dlp_options_configures_supported_bgutil_provider(tmp_path):
    from auralis.acquisition import _yt_dlp_options

    options = _yt_dlp_options(
        tmp_path / "raw",
        pot_base_url="http://127.0.0.1:4416",
    )

    assert options["extractor_args"] == {
        "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]}
    }
    assert "po_token" not in options


def test_yt_dlp_options_uses_environment_provider(tmp_path, monkeypatch):
    from auralis.acquisition import _yt_dlp_options

    monkeypatch.setenv("AURALIS_YTDLP_POT_BASE_URL", "http://pot:4416")
    options = _yt_dlp_options(tmp_path / "raw")
    assert options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://pot:4416"]


def test_yt_dlp_options_uses_configured_cookie_file(tmp_path, monkeypatch):
    from auralis.acquisition import _yt_dlp_options

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("AURALIS_YTDLP_COOKIES_FILE", str(cookie_file))
    options = _yt_dlp_options(tmp_path / "raw")
    assert options["cookiefile"] == str(cookie_file)


def test_yt_dlp_options_falls_back_to_local_cookie_file(tmp_path, monkeypatch):
    from auralis.acquisition import _yt_dlp_options

    monkeypatch.delenv("AURALIS_YTDLP_COOKIES_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    options = _yt_dlp_options(tmp_path / "raw")
    assert options["cookiefile"] == str(cookie_file.resolve())


def test_yt_dlp_options_converts_local_json_cookie_export(tmp_path, monkeypatch):
    from auralis.acquisition import _yt_dlp_options

    monkeypatch.delenv("AURALIS_YTDLP_COOKIES_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    cookie_file = tmp_path / "youtube_cookies.json"
    cookie_file.write_text(
        '[{"domain": ".youtube.com", "path": "/", "secure": true, '
        '"expirationDate": 1822425245, "name": "TEST_COOKIE", "value": "secret"}]',
        encoding="utf-8",
    )
    options = _yt_dlp_options(tmp_path / "raw")
    converted = Path(options["cookiefile"])
    assert converted.exists()
    assert converted.suffix == ".txt"
    assert "TEST_COOKIE" in converted.read_text(encoding="utf-8")
    assert "secret" in converted.read_text(encoding="utf-8")


def test_yt_dlp_options_ignores_missing_cookie_file(tmp_path, monkeypatch):
    from auralis.acquisition import _yt_dlp_options

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AURALIS_YTDLP_COOKIES_FILE", raising=False)
    options = _yt_dlp_options(tmp_path / "raw")
    assert "cookiefile" not in options


def test_sha256_is_stable(tmp_path: Path) -> None:
    from auralis.acquisition import _sha256

    path = tmp_path / "audio.webm"
    path.write_bytes(b"auralis-audio")
    assert _sha256(path) == _sha256(path)


def test_provider_health_opens_and_expires() -> None:
    from auralis.acquisition import ProviderHealth

    health = ProviderHealth(cooldown_seconds=0)
    health.open("youtube")
    assert health.allow("youtube")
    health.close("youtube")
    assert health.allow("youtube")


def test_asset_manifest_contains_integrity_fields(tmp_path: Path) -> None:
    asset = AudioAsset(
        source_id="xyz",
        source_url="https://example.com/audio",
        path="/tmp/xyz.webm",
        acquired_at="2026-08-29T00:00:00+00:00",
        size_bytes=2048,
        sha256="abc123",
        provider="generic",
    )
    manifest = write_manifest(asset, tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["sha256"] == "abc123"
    assert payload["provider"] == "generic"
