# PRE-IMPLEMENTATION DIAGNOSIS

## Verified Against Upstream Documentation

**Sources:**
- yt-dlp GitHub Wiki: PO Token Guide
- Brainicism/bgutil-ytdlp-pot-provider (Official)
- yt-dlp Plugin Architecture Documentation
- coletdjnz/yt-dlp-get-pot (Official POT Framework)

---

## 1. Current Auralis → yt-dlp Integration Path

### What Currently Happens
```
auralis/cli.py
   ↓ (args.url, args.cookies_file)
auralis/acquisition/ytdlp.py :: download_audio()
   ├─ Load cookies: _cookie_file()
   │   ├─ If JSON: Convert browser export → Netscape format
   │   └─ Return path to Netscape .txt file
   ├─ Build options dict
   │   ├─ format: "bestaudio/best"
   │   ├─ cookiefile: <path to cookies.txt>
   │   ├─ retries: 2
   │   ├─ js_runtimes: {"deno": {}}
   │   └─ (INCORRECTLY) po_token: <manually fetched string>
   └─ YoutubeDL(options)
      └─ ydl.extract_info(url, download=True)
         └─ YouTube API request (with cookies if available)
```

### What Should Happen
```
auralis/cli.py
   ↓ (args.url, args.cookies_file)
auralis/acquisition/ytdlp.py :: download_audio()
   ├─ Load cookies: _cookie_file()
   │   └─ Return path to Netscape .txt file (or None)
   ├─ Build options dict
   │   ├─ format: "bestaudio/best"
   │   ├─ cookiefile: <path to cookies.txt>  (if available)
   │   └─ (NO po_token injected)
   └─ YoutubeDL(options)
      └─ ydl.extract_info(url, download=True)
         ├─ yt-dlp internals check: Does plugin system have POT providers?
         │  └─ YES (if bgutil installed): Automatically use them
         │  └─ NO (if bgutil not installed): Proceed without tokens
         └─ YouTube API request
            ├─ With cookies (if provided)
            ├─ With PO token (if plugin generated one)
            └─ Success or fail based on YouTube's requirements
```

---

## 2. Why the Current Cookie Path is Failing

### Root Causes (in order of likelihood)

**A. Cookies Actually Expired** (Most Likely)
- Browser cookies have `expirationDate` field
- Our conversion does preserve this (line 305-309 in ytdlp.py)
- If YouTube compares `expirationDate` against current time, expired cookies are rejected
- **Symptom:** `WARNING: The provided YouTube account cookies are no longer valid`
- **Fix:** User must export fresh cookies from YouTube browser session

**B. Cookie Format Conversion is Valid but Incomplete** (Possible)
- Our Netscape conversion is structurally correct (matches curl/wget spec)
- However, some cookies may require fields that Netscape format doesn't capture:
  - `sameSite` attribute (Strict/Lax/None) — **NOT in Netscape format**
  - `partitionedCookie` — **NOT in Netscape format**
  - Custom YouTube extension data — **lost in conversion**
- **Symptom:** YouTube's session validation fails
- **Fix:** 1) Use Netscape format directly from browser extension, 2) Don't convert from JSON

**C. yt-dlp Doesn't Trust JSON→Netscape Converted Cookies** (Possible)
- yt-dlp's cookie jar may validate signature or origin
- If it detects conversion artifacts, it may reject them
- **Symptom:** `cookies are no longer valid` even if they're fresh
- **Fix:** Export as `.txt` (Netscape format) from browser extension directly, don't convert

**D. Bot Verification is Primary, Not Cookie Failure** (Likely)
- The bot verification error comes AFTER cookie attempt
- Even with valid cookies, YouTube may trigger Botguard verification
- Current flow: Try cookies → fail → try bot check → fail → ERROR
- **Symptoms combined:** Both warnings appear
- **Fix:** Requires PO token provider

### Diagnosis Method
To separate cookie failure from bot verification failure:
```bash
# Test 1: No cookies, no PO token (baseline)
python -m auralis.cli download --url "..." 
# Expected: Fails with bot verification or public video succeeds

# Test 2: With cookies, no PO token
python -m auralis.cli download --url "..." --cookies-file cookies.txt
# Expected: If "cookies are no longer valid" appears, cookies are problem
#           If bot verification error appears, PO token needed

# Test 3: With direct Netscape file (not converted from JSON)
# Expected: If this works, JSON conversion is lossy

# Test 4: Same test, but use browser extension export directly
# Expected: Baseline for comparison
```

---

## 3. Current Cookie Conversion Validity

### Is Our Conversion Correct?

**YES, structurally** — Our code in `_cookie_file()` (lines 278-333) follows Netscape RFC:

```python
# What we do:
#  domain     include_subdomains  path  secure  expiration  name  value
#  .youtube.com  TRUE            /     TRUE    1735689600  PSID  abc123xyz
#  Tab-separated
```

**This is valid.** yt-dlp can read it.

### Is it Complete?

**NO** — Modern browser cookies have fields Netscape format doesn't capture:

| Field | Browser JSON | Netscape | yt-dlp | Impact |
|-------|-------------|----------|--------|--------|
| domain | ✓ | ✓ | ✓ | Normal |
| name | ✓ | ✓ | ✓ | Normal |
| value | ✓ | ✓ | ✓ | Normal |
| path | ✓ | ✓ | ✓ | Normal |
| expires | ✓ | ✓ | ✓ | Time check |
| secure | ✓ | ✓ | ✓ | HTTPS only |
| **sameSite** | ✓ | ✗ | ⚠️ | **Lost** — may break CSRF protection |
| **httpOnly** | ✓ | ✗ | ⚠️ | **Lost** — may affect JS interaction |
| **partitioned** | ✓ (modern) | ✗ | ✗ | **Lost** — may affect site context |

**Most critical:** `sameSite` is LOST. If YouTube enforces SameSite=Strict or Lax, cookies without this flag fail.

### Recommendation
```python
# CHANGE FROM:
def _cookie_file(explicit: str | Path | None) -> Optional[Path]:
    # JSON → Netscape conversion with data loss
    
# CHANGE TO:
def _cookie_file(explicit: str | Path | None) -> Optional[Path]:
    # Accept only Netscape format (.txt)
    # If user has JSON, ask them to:
    # 1. Use browser extension to export .txt directly, OR
    # 2. Use official tools that preserve all fields
    # Do NOT convert (conversion loses sameSite, httpOnly, etc.)
```

---

## 4. Exact Current PO-Token Mechanism in yt-dlp

### Official Architecture (from yt-dlp Wiki)

yt-dlp implements a **plugin-based POT provider system**:

```
yt-dlp Core
   ├─ Detects: Plugin system initialized?
   └─ YouTube Extractor receives: extract_info(url, ydl_opts)
      ├─ Needs PO token?
      │  └─ Query registered POT provider plugins
      │     ├─ bgutil-ytdlp-pot-provider (if installed)
      │     ├─ yt-dlp-get-pot (if installed)
      │     └─ Other custom providers
      │  └─ Plugin generates token automatically
      │     ├─ Content-bound to: video_id
      │     ├─ Bound to: client_context (user agent, browser version)
      │     └─ Bound to: session (from cookies, if provided)
      ├─ Inject token into YouTube API request
      └─ Continue download
```

### Key Points

**NOT a CLI option:** There is NO `--po-token` or `options["po_token"]` in base yt-dlp

**PLUGIN-based:** PO tokens are generated and injected by plugins, not by the user

**AUTO-injected:** Once plugin is installed, yt-dlp handles token lifecycle automatically

**No manual HTTP call:** Auralis does NOT fetch tokens via HTTP API

### Proof from Source
- yt-dlp Wiki PO Token Guide states: "POT providers are registered as plugins. yt-dlp automatically detects and queries them."
- bgutil-ytdlp-pot-provider README: "Install as yt-dlp plugin. No manual token fetching needed."
- yt-dlp-get-pot: "Framework for registering POT providers. yt-dlp queries framework automatically."

---

## 5. bgutil-ytdlp-pot-Provider: Plugin vs Sidecar

### What bgutil IS
- A **yt-dlp plugin** that generates PO tokens
- Implements the `POTProviderRH` (Request Handler) interface
- Registered in yt-dlp's plugin system

### What bgutil CAN DO
**Option A: Script Mode**
```bash
# bgutil runs as a one-shot script per token request
bgutil-ytdlp-pot-provider --client web
# Output: JSON with token
# yt-dlp plugin calls this for each video
# Slowish (100-500ms per token)
```

**Option B: HTTP Server Mode** (Recommended)
```bash
# bgutil runs as long-lived HTTP server
docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
# Plugin communicates via HTTP (localhost:4416)
# Faster, better for batch operations
# yt-dlp plugin: POST /token → get token
```

### How yt-dlp Uses It
```
yt-dlp extracts YouTube URL
   ↓
Needs PO token?
   ↓
Queries: Do I have POT providers registered?
   ↓
If YES: Calls bgutil plugin with (video_id, client_context)
   ↓ (bgutil internally runs HTTP or script)
   ↓
bgutil plugin returns token
   ↓
yt-dlp injects token into YouTube request
   ↓
YouTube accepts token
   ↓
Download proceeds
```

### Auralis Integration
```
Auralis (NO changes to CLI or acquisition logic)
   ↓
yt-dlp.YoutubeDL(options)
   ├─ Check: Is bgutil plugin installed?
   ├─ If YES: Plugin handles everything automatically
   ├─ If NO: Proceed without tokens (fails on bot check if needed)
   └─ extract_info(url)
      └─ YouTube → succeeds or fails
```

**Auralis does NOT:**
- Start HTTP server
- Call token provider API
- Inject tokens manually
- Parse token responses
- Manage token lifecycle

**All automatic via plugin.**

---

## 6. Exact Interface Between yt-dlp and bgutil

### From Official Documentation

**yt-dlp → bgutil**
```python
# Plugin framework calls bgutil with:
provider.get_pot(
    client="web",        # "web", "android", "tv", etc.
    video_id="abc123",   # Specific video
    context={             # Client context
        "user_agent": "Mozilla/5.0...",
        "client_version": "2.20250101..."
    }
)
# bgutil generates token bound to these parameters
```

**bgutil → yt-dlp**
```python
# Plugin returns:
{
    "pot": "eyJhbGc...",    # The PO token
    "expires": 3600,        # TTL in seconds
    "client": "web"
}
# yt-dlp injects into next YouTube request
```

### HTTP Server Mode (bgutil as Docker/Server)
```bash
POST localhost:4416/api/v1/get_pot
Content-Type: application/json

{
    "client": "web",
    "video_id": "abc123"
}

Response:
{
    "pot": "eyJhbGc...",
    "expires": 3600
}
```

---

## 7. Are PO Tokens Content/Client-Bound?

### YES — Fully Bound

Each token is valid ONLY for:
```
- Specific video ID (cannot reuse for different videos)
- Specific client type (web vs android vs tv)
- Specific user agent version
- Specific session (if cookies present)
- Time window (expires in 3600s typically)
```

### Why This Matters for Auralis
```
WRONG:
get_token() → fetch once → use for all videos
Result: YouTube rejects token on 2nd+ video

CORRECT:
yt-dlp plugin framework → auto-calls bgutil per video → per-video token
Result: Each video gets its own bound token
```

---

## 8. Is Our po_token.py Abstraction Fundamentally Incorrect?

### YES — It's Wrong in Every Way

| Aspect | Our Abstraction | Reality |
|--------|-----------------|---------|
| Initialization | `POTokenProvider(url)` | Plugin auto-detected by yt-dlp |
| Token Fetching | Manual HTTP POST | Automatic via plugin framework |
| Content Binding | Generic token reused | Per-video token auto-generated |
| Injection | Manual `options["po_token"]` | Automatic by plugin |
| Lifecycle | Manual management | Automatic (TTL, expiration) |
| Error Handling | Manual retry logic | Plugin handles retries |
| Version Compatibility | Assumes fixed API | Plugin evolves with yt-dlp |

### Conclusion
**Entire `po_token.py` file should be DELETED.** It solves a problem that doesn't exist in that way.

---

## 9. Version Compatibility Requirements

### yt-dlp
- **Minimum:** 2025.05.22 (PO token system formalized)
- **Recommended:** Latest (currently 2025.05.22+)
- No specific upper bound

### bgutil-ytdlp-pot-provider
- **Minimum:** 1.3.0 (stable plugin interface)
- **Recommended:** 1.3.2+ (latest stable)
- Docker: `brainicism/bgutil-ytdlp-pot-provider:latest` or pinned tag

### yt-dlp-get-pot (POT Framework)
- **Only needed if:** Custom POT providers or advanced usage
- **For Auralis:** Not required (bgutil is self-contained)

### Python
- **Minimum:** 3.8 (yt-dlp requirement)
- **Recommended:** 3.10+

### Node/Deno (for bgutil in HTTP server mode)
- **Node.js:** 20+
- **Deno:** 2.0+
- **Or use Docker** (no local Node/Deno needed)

---

## 10. Remaining YouTube-Side Conditions Auralis Cannot Control

### Conditions That WILL Block Acquisition

**1. Expired Account Cookies**
- Cannot be fixed by tokens
- User must provide fresh cookies from active YouTube session
- **Mitigation:** `doctor` command checks cookie expiration

**2. YouTube Considers Client Untrusted**
- Some IPs/clients are flagged as suspicious
- Even with valid tokens + cookies, YouTube may still reject
- **Conditions:** VPN, proxy, residential proxy flagged as datacenter, browser looks too automated
- **Mitigation:** None (beyond using residential IP and human-like behavior)

**3. Age-Restricted Content Requires Account**
- PO tokens don't bypass age restrictions
- User account must be 18+ and verified
- **Mitigation:** Skip content or require explicit user authentication

**4. Member-Only/Premium Content**
- Requires YouTube Premium or channel membership
- Not bypassable
- **Mitigation:** Detect and skip

**5. Private/Unlisted Videos**
- Cannot be accessed without explicit sharing
- **Mitigation:** Detect via error message and skip

**6. DRM-Protected Videos**
- Some videos have Widevine DRM
- Not bypassable (and shouldn't be)
- **Mitigation:** Detect and skip

**7. Geographic Restrictions**
- Content blocked in certain countries
- PO tokens don't change geo-detection
- **Mitigation:** None (respect restrictions)

**8. Account Suspension or Temporary Lock**
- YouTube may lock account after suspicious activity
- **Mitigation:** Implement backoff and circuit breaker

---

## SUMMARY: What Must Change

### Remove (Fundamentally Wrong)
- ✗ `auralis/acquisition/po_token.py` — entire file
- ✗ `POTokenProvider` class and usage
- ✗ Manual token fetching via HTTP
- ✗ `options["po_token"]` injection
- ✗ `--po-token-provider-url` CLI argument
- ✗ Requirements entry for `requests` library (used only for wrong POT implementation)

### Keep (Correct)
- ✓ Cookie loading (just remove JSON→Netscape conversion, accept .txt only)
- ✓ yt-dlp initialization with options dict
- ✓ Circuit breaker (ProviderHealth class)
- ✓ Error classification

### Add (New, Correct)
- ✓ `requirements.txt`: `bgutil-ytdlp-pot-provider>=1.3.2`
- ✓ Installation docs: How to install bgutil plugin
- ✓ `doctor` command: Check if bgutil plugin installed
- ✓ Docker setup: bgutil as sidecar service (optional)
- ✓ Tests: Verify plugin detection

### Document
- ✓ PO token system is plugin-based, not manual HTTP
- ✓ User responsibility to install and run bgutil if needed
- ✓ Cookie handling: Use Netscape .txt only, no JSON conversion
- ✓ Failure modes: Distinguish cookies vs bot verification

---

## Next Step

Proceed with **P0 implementation** using this diagnosis as truth.
