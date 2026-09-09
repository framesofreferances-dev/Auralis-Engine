from auralis.discovery import relevance, score_candidate
from auralis.models import Candidate


def test_relevance_rewards_matching_terms() -> None:
    assert relevance("eerie ambient", "Eerie Ambient Soundscape") == 1.0


def test_score_is_bounded() -> None:
    candidate = Candidate(
        id="x",
        title="eerie ambient",
        url="https://example.com/x",
        view_count=1_000_000,
        upload_date="20260824",
    )
    score = score_candidate("eerie ambient", candidate)
    assert 0 <= score <= 100


def test_download_error_classification() -> None:
    from auralis.acquisition import _classify_download_error

    assert _classify_download_error(Exception("Sign in to confirm you’re not a bot")) == "provider verification required"
    assert _classify_download_error(Exception("Video unavailable")) == "source unavailable"


def test_ytdlp_options_enable_deno(tmp_path) -> None:
    from auralis.acquisition import _yt_dlp_options

    assert _yt_dlp_options(tmp_path)["js_runtimes"] == {"deno": {}}
