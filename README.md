# Auralis Engine

**Intelligent Audio Discovery & Orchestration Engine**

Auralis is an isolated audio intelligence subsystem designed for automated short-form video pipelines.

Its job is to turn a semantic music request into a ranked audio candidate, observe how candidates change over time, acquire an available audio stream, and prepare it for downstream mixing. The production video pipeline remains completely separate until Auralis is proven stable.

## Current scope

- Semantic YouTube audio discovery using `yt-dlp`'s public search extractor.
- Metadata enrichment without media download.
- Transparent relevance / freshness / popularity scoring.
- Local SQLite observations for measuring candidate changes over repeated scans.
- Trend scoring that prefers measured view velocity when historical observations exist and uses an explicit age-adjusted proxy before history is available.
- Structured JSON metadata for later integration with the production pipeline.
- Audio acquisition through yt-dlp with Deno-enabled extraction, bounded retries, and ranked-candidate fallback.
- FFmpeg-based duration trimming, looping, normalization and fade processing.
- Narration ducking and final audio mixing.
- CLI-first design so every stage can be tested independently.
- GitHub Actions CI for automated regression tests.

## Architecture

```text
Music query
    |
    v
+------------------+
| Trend Discovery  |
+--------+---------+
         |
         v
+---------------------+
| Metadata Enrichment |
+----------+----------+
           |
           +-------> SQLite observations
           |
           v
+------------------+
| Trend Intelligence|
+--------+---------+
         |
         v
+------------------+
| Candidate Ranking |
+--------+---------+
         |
         v
+------------------+
| Audio Acquisition |
+--------+---------+
         |
         v
+------------------+
| FFmpeg Processing |
+--------+---------+
         |
         v
+------------------+
| Narration Mixer  |
+--------+---------+
         |
         v
      Final audio
```

## Requirements

- Python 3.11+
- FFmpeg available on `PATH`
- Internet access for discovery/acquisition
- `yt-dlp` kept current when running against YouTube

## Quick start

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m auralis.cli search "eerie ambient technology"
```

Run the trend pipeline:

```bash
python -m auralis.cli trend "viral cinematic background music" --limit 10
```

JSON output is available for automation:

```bash
python -m auralis.cli trend "viral cinematic background music" --limit 10 --json
```

The default local observation database is `data/auralis.db`. It is intentionally ignored by Git so each environment can maintain its own observation history.

To acquire a selected result:

```bash
python -m auralis.cli download --url "YOUTUBE_URL"
```

To let Auralis try ranked discovery results until one permitted source is available:

```bash
python -m auralis.cli acquire "cinematic background music" --limit 10
```

## Trend model

Auralis keeps discovery relevance separate from trend intelligence. The current trend score combines:

- discovery relevance/popularity,
- recency,
- measured views-per-day when two observations exist,
- an age-adjusted view-velocity proxy before historical data exists,
- lightweight engagement rate from likes and comments.

The model is intentionally conservative: it does not claim that lifetime views equal current trend velocity, and it does not fabricate historical data that Auralis has not observed.

## Design principle

Auralis is deliberately **not** part of the production repository yet. It is a laboratory and reusable engine. The production pipeline should only consume a stable interface after discovery, trend intelligence, acquisition, processing and mixing have passed automated tests.

Auralis does not attempt to bypass DRM, authentication, paywalls, access controls, rate limits, or platform restrictions. Resilience is implemented through separation of stages, bounded operations and graceful fallback when metadata is unavailable. Availability and licensing remain properties of the selected source.


## YouTube acquisition provider

Auralis delegates Proof-of-Origin token generation to yt-dlp's supported PO-token provider framework rather than fetching or caching tokens in application code. The current recommended provider is `bgutil-ytdlp-pot-provider`, installed as a yt-dlp plugin. yt-dlp's current documentation recommends provider plugins because PO tokens can be content-bound and should be generated for the request context.

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

For local development, run the provider sidecar:

```bash
docker run --name bgutil-provider -d --init -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider:1.3.2
```

Then either use the default provider endpoint or configure Auralis explicitly:

```bash
export AURALIS_YTDLP_POT_BASE_URL=http://127.0.0.1:4416
python -m auralis.cli doctor
```

The provider is configured through yt-dlp's extractor arguments; Auralis does **not** call `/get_pot` itself and does not store PO tokens.

Docker Compose starts Auralis and the provider together:

```bash
docker compose up -d --build
docker compose ps
docker compose logs bgutil-provider
```

For a direct verbose verification, inspect yt-dlp's provider registry:

```bash
python -m yt_dlp -v "https://www.youtube.com/watch?v=VIDEO_ID"
```

A healthy installation should show a `[pot]` line listing the bgutil provider.

### Cookies are a separate layer

A valid cookie session and a PO token solve different problems. A PO provider **does not refresh, repair, or replace invalid YouTube cookies**. If yt-dlp reports that the supplied account cookies have been rotated or are no longer valid, export a new authorized cookie session and test that independently.

Auralis can consume Netscape `cookies.txt` directly and can convert a browser JSON export to Netscape format for compatibility. The conversion does not make an expired or rotated session valid.

Run the diagnostic command at any time:

```bash
python -m auralis.cli doctor
```



## One-command music acquisition

For local development with the working YouTube cookie + bgutil setup:

```bash
python -m auralis.cli get-music "cinematic background music" \
  --cookies-file "$PWD/youtube_cookies.txt" \
  --pot-base-url "http://127.0.0.1:4416"
```

This discovers candidates, acquires the first available permitted source into
`assets/raw/`, converts it to high-quality MP3 with FFmpeg, and places the
ready-to-use track in `music/`. The cookie file is used only locally and is
never written to manifests.

You can also convert an existing asset independently:

```bash
python -m auralis.cli convert "assets/raw/VIDEO_ID.webm"
```
