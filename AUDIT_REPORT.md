# AURALIS ENGINE — CRITICAL ARCHITECTURE AUDIT REPORT

**Status:** ⚠️ **PRODUCTION FAILURE** — PO token implementation is architecturally incorrect

**Date:** 2026-08-29  
**Test Case:** `python -m auralis.cli download --url "https://www.youtube.com/watch?v=WQmGwmc-XUY" --cookies-file "$PWD/youtube_cookies.json"`

**Result:** Still fails with:
```
WARNING: [youtube] The provided YouTube account cookies are no longer valid.
ERROR: [youtube] WQmGwmc-XUY: Sign in to confirm you're not a bot.
```

---

## ROOT CAUSE ANALYSIS

### Critical Finding #1: PO Token Implementation is Fundamentally Wrong

**Current Implementation (WRONG):**
```python
# auralis/acquisition/ytdlp.py line 366
options["po_token"] = po_token  # ← INCORRECT
```

**Why This is Wrong:**

1. **yt-dlp does NOT accept `po_token` as a YoutubeDL option parameter**
   - The `po_token` is NOT a direct yt-dlp configuration key
   - yt-dlp expects PO tokens to be provided via its **plugin framework**, not as a constructor option
   - The option key should be passed to yt-dlp via CLI as `--po-token`, but the Python API requires a different mechanism

2. **bgutil-ytdlp-pot-provider is NOT an HTTP API you call manually**
   - It is a **yt-dlp plugin** that integrates into yt-dlp's POT (Proof-of-Origin Token) provider framework
   - The correct flow is:
     ```
     yt-dlp (with plugin installed)
         ↓
     Needs PO token?
         ↓
     Plugin framework queries registered POT providers
         ↓
     bgutil-ytdlp-pot-provider plugin generates token internally
         ↓
     Token automatically injected into YouTube requests
     ```
   - Our implementation tries to manually fetch a token via HTTP and inject it, which:
     - Breaks the plugin contract
     - Creates token lifetime/binding issues
     - Doesn't match how yt-dlp actually uses PO tokens

3. **PO Tokens ARE Content-Bound**
   - Each PO token is bound to:
     - The specific video ID
     - The specific client context (user agent, client version)
     - The session/cookie state
     - Possibly a timestamp window
   - Fetching ONE generic token and reusing it for all downloads will NOT work
   - Our abstraction `get_token() -> str` is architecturally inadequate

4. **The Plugin Architecture is Auto-Managed**
   - When bgutil-ytdlp-pot-provider is properly installed as a plugin, yt-dlp:
     - Automatically detects it via the plugin registry
     - Automatically calls it when YouTube requires bot verification
     - Passes the video ID, client context, and session state to the plugin
     - The plugin returns a valid token bound to that context
   - There is NO manual HTTP token-fetching step

---

### Critical Finding #2: CLI Does NOT Pass PO Token Provider URL

**Current Implementation (WRONG):**
```python
# auralis/cli.py line 1-13
from .acquisition import acquire_first_available, download_audio, POTokenProvider
```

**Issue:**
- CLI imports `POTokenProvider` but **never instantiates or passes it to acquisition functions**
- Look at lines 136-144 in cli.py: the `download` command ignores `--po-token-provider-url`
- Look at lines 125-133 in cli.py: the `acquire` command ignores `--po-token-provider-url`
- The parser defines `--po-token-provider-url` but it's **never actually used**

**Proof:**
```python
# cli.py line 136-144 (CURRENT - WRONG)
if args.command == "download":
    path = download_audio(
        args.url,
        Path(args.output_dir),
        manifest_dir=Path(args.manifest_dir),
        cookies_file=args.cookies_file,
        # ← po_token_provider NOT PASSED
    )
```

---

### Critical Finding #3: requirements.txt is NOT Updated

**Current requirements.txt:**
```
yt-dlp
pytest
```

**What was CLAIMED to be added:**
```
yt-dlp>=2025.05.22
pytest>=7.0
requests>=2.31.0
```

**Actual File on Disk:**
The file shows only `yt-dlp` and `pytest` with no version pins or `requests`.

**Why This Matters:**
- `requests` is imported in `po_token.py` but NOT listed in requirements
- The package cannot be installed correctly
- Even if it could, the API it uses doesn't match upstream

---

### Critical Finding #4: Cookie Parsing May Be Lossy

**Current Implementation:**
```python
# auralis/acquisition/ytdlp.py lines 278-333
# Converts browser JSON cookies → Netscape format
```

**Potential Data Loss:**
1. **SameSite Attributes:** Browser cookies may have `sameSite` (Strict/Lax/None), but Netscape format has no field for this
2. **Partitioned Cookies:** Modern browsers support cookie partitioning; Netscape format doesn't capture this
3. **HttpOnly Flag:** Captured but not used by yt-dlp in the same way browsers do
4. **Session Cookies:** If `expirationDate` is missing or 0, they may be treated as expired
5. **Cookie Ordering:** If YouTube expects cookies in a specific order, our sorting may break it
6. **Domain Cookie Semantics:** `.youtube.com` vs `youtube.com` handling may differ

**Diagnosis:**
```
"The provided YouTube account cookies are no longer valid"
```

This error could mean:
- A. Cookies actually expired
- B. Cookie format conversion lost critical fields
- C. yt-dlp doesn't trust Netscape format from JSON conversion
- D. YouTube's session validation requires fields not in Netscape format

**We don't know which without testing.**

---

### Critical Finding #5: No Diagnostic Capability

**Missing:** A way to answer:
1. Are cookies actually loaded into yt-dlp?
2. Is yt-dlp sending them to YouTube?
3. Does YouTube accept them?
4. Is the PO token provider running?
5. Is the PO token actually being used?
6. What is the exact error from YouTube?

---

## CORRECT ARCHITECTURE

### The Right Way: Install bgutil-ytdlp-pot-provider as a Plugin

```
Requirements:
1. bgutil-ytdlp-pot-provider installed as yt-dlp plugin
2. Provider running as HTTP service (port 4416 default, or configurable)
3. yt-dlp auto-detects plugin and registers it
4. When YouTube triggers bot check, plugin generates token
5. Token auto-bound to video ID + client context
6. Download succeeds

Auralis does NOT manually fetch tokens.
```

### Corrected Implementation Flow

```python
# Step 1: Initialize yt-dlp with plugin support
options = {
    "format": "bestaudio/best",
    "outtmpl": str(destination / "%(id)s.%(ext)s"),
    "quiet": False,
    # ← NO manual po_token injection
    # ← Plugin handles it automatically
}

# Step 2: If cookies provided, add them (unchanged)
if cookie_file is not None:
    options["cookiefile"] = str(cookie_file)

# Step 3: Extract (unchanged - plugin handles tokens)
with YoutubeDL(options) as ydl:
    info = ydl.extract_info(url, download=True)
```

### Corrected Architecture Diagram

```
┌─────────────────────────────────┐
│    Auralis CLI                  │
│  python -m auralis.cli download │
│    --url <URL>                  │
│    --cookies-file <COOKIES>     │
└────────────┬────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│  auralis/acquisition/ytdlp.py    │
│  download_audio()                │
│  ├─ Load cookies (if provided)   │
│  ├─ Build yt-dlp options         │
│  └─ Execute YoutubeDL()          │
└────────────┬─────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│  yt-dlp (with plugins loaded)    │
│  ├─ Detects bgutil plugin        │
│  ├─ YouTube requests video       │
│  ├─ Bot verification triggered?  │
│  │   YES → Call plugin           │
│  │       ├─ Plugin sends HTTP    │
│  │       │  to provider:4416     │
│  │       ├─ Provider generates   │
│  │       │  video-specific token │
│  │       └─ Plugin receives &    │
│  │           injects token       │
│  │   NO → Continue normally      │
│  └─ Download succeeds            │
└────────────┬─────────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│  bgutil-ytdlp-pot-provider       │
│  (Running on localhost:4416)     │
│  ├─ Listens for plugin requests  │
│  ├─ Generates PO token for video │
│  └─ Returns token to plugin      │
└──────────────────────────────────┘
```

---

## WHAT NEEDS TO CHANGE

### 1. **Remove Manual PO Token Fetching (WRONG APPROACH)**

Delete or deprecate:
- `auralis/acquisition/po_token.py` (entire file - WRONG abstraction)
- `POTokenProvider` class usage from `ytdlp.py`
- All HTTP token fetching logic

### 2. **Fix CLI** (Currently broken)

The CLI argument `--po-token-provider-url` should NOT exist.

Instead, add diagnostic commands:

```bash
python -m auralis.cli doctor
```

This should report:
- Is bgutil-ytdlp-pot-provider installed?
- Is provider service running (if using HTTP mode)?
- Can yt-dlp detect the plugin?

### 3. **Fix requirements.txt**

```
yt-dlp>=2025.05.22
pytest>=7.0
bgutil-ytdlp-pot-provider>=1.3.2
```

Note: Install bgutil as a **dependency**, not as an optional manual service.

### 4. **Fix Cookie Handling**

**Option A (Recommended):** Accept `.txt` (Netscape format) only
```python
def _cookie_file(explicit: str | Path | None = None) -> Optional[Path]:
    """Resolve Netscape-format cookie file."""
    # Try provided path first
    if explicit and Path(explicit).exists():
        return Path(explicit)
    
    # Then check standard locations
    for candidate in [Path.cwd() / "cookies.txt", ...]:
        if candidate.exists():
            return candidate
    
    return None
    # ← No JSON conversion; user must provide Netscape format
```

**Option B (Thorough):** Validate JSON export format before conversion
```python
def _validate_browser_cookies(data: list) -> bool:
    """Verify JSON structure before conversion."""
    required_fields = {"domain", "name", "value"}
    for cookie in data:
        if not isinstance(cookie, dict):
            return False
        if not required_fields.issubset(cookie.keys()):
            return False
    return True
```

### 5. **Add Real Diagnostic Command**

```bash
python -m auralis.cli doctor
```

Output:
```
Auralis Engine Doctor
────────────────────────────────

Python              ✓ 3.12.0
yt-dlp              ✓ 2025.05.22
FFmpeg              ✓ 7.0.1
Deno                ✓ 2.0.0

Plugins
  bgutil installed  ✓ v1.3.2
  Plugin registry   ✓ 1 POT provider detected

PO Provider Service
  HTTP available    ✗ Cannot reach localhost:4416
  Recommendation    $ docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider

Cookies
  file provided     ✗ None
  file format       N/A
  YouTube auth      ⚠ No cookies - downloads may fail on restricted videos

YouTube Test
  Can reach API     ✓
  Search works      ✓ (e.g., "test music")
  Format discovery  ⚠ Not tested (no URL provided)

Overall Status: DEGRADED

To enable PO token support:
1. docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
2. Verify: yt-dlp -v https://www.youtube.com/watch?v=test 2>&1 | grep "pot"

To enable cookie-based authentication:
1. Export cookies from browser: https://github.com/yt-dlp/yt-dlp#cookies
2. Save as cookies.txt in current directory OR
3. Pass: python -m auralis.cli download --url ... --cookies-file /path/to/cookies.txt
```

---

## HONEST ROOT CAUSE SUMMARY

### Why the Command Still Fails

```
Root Cause Chain:

1. Missing bgutil-ytdlp-pot-provider plugin installation
   └─→ yt-dlp has no way to generate PO tokens
   └─→ YouTube bot check fails
   └─→ ERROR: "Sign in to confirm you're not a bot"

2. Cookie parsing may have lost fields during JSON→Netscape conversion
   └─→ yt-dlp cannot authenticate
   └─→ YouTube rejects cookies
   └─→ WARNING: "cookies are no longer valid"

3. Manual PO token fetching (if it were running) wouldn't work because:
   └─→ Tokens are content-bound (per-video)
   └─→ Our abstraction tries to reuse one token for all videos
   └─→ YouTube verification fails
   └─→ Download blocked

4. CLI doesn't actually pass PO provider URL to acquisition layer
   └─→ Even if implementation were correct, it's not used
   └─→ Token provider never contacted
   └─→ No tokens available
```

### The Cookie Error is Separate From PO Token Error

These are TWO independent failures:

| Issue | Cause | Fix |
|-------|-------|-----|
| "cookies are no longer valid" | Browser JSON → Netscape conversion may be lossy OR cookies actually expired | 1. Test with fresh browser export 2. Use Netscape format directly 3. Validate conversion logic |
| "Sign in to confirm you're not a bot" | No PO token provider installed/running | Install bgutil-ytdlp-pot-provider as yt-dlp plugin + run HTTP service |

**Both must work together** for restricted/bot-protected videos.

---

## REMAINING BLOCKERS (If Everything is Fixed)

1. **YouTube's Anti-Bot Mechanisms May Be Stronger Than Expected**
   - Even with valid cookies + valid PO token, YouTube may reject if:
     - IP address is flagged as proxy/VPN
     - User agent doesn't match cookie origin
     - Request pattern looks automated
     - Account has insufficient trust
   - **This is beyond Auralis control.** Would require:
     - Residential IP address
     - Browser-like request patterns (slow, human-paced)
     - Account aging/warm-up
     - Handling rate limits gracefully

2. **Video May Be Legitimately Restricted**
   - Age-restricted content requires YouTube Premium or age verification
   - Member-only videos require membership
   - Private videos require explicit sharing
   - These cannot be bypassed by tokens or cookies

3. **Deno JS Runtime Issue**
   - Current config: `"js_runtimes": {"deno": {}}`
   - Deno may not be installed or may have signature timestamp issues
   - Solution: Remove this if not needed, or use native JS runtime
   - **Likely not the primary blocker** but should be tested

---

## IMPLEMENTATION PLAN (Priority Order)

### P0 — Fix the Broken Implementation

1. **Remove** `auralis/acquisition/po_token.py` (entire file)
2. **Remove** `POTokenProvider` from `__init__.py`
3. **Remove** `po_token_provider` parameter from `download_audio()` and `acquire_first_available()`
4. **Remove** `--po-token-provider-url` from CLI
5. **Remove** manual token fetching logic
6. **Update** requirements.txt to include `bgutil-ytdlp-pot-provider>=1.3.2`
7. **Add documentation** explaining:
   - bgutil-ytdlp-pot-provider must be installed
   - Provider HTTP service must be running (`docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider`)
   - yt-dlp will auto-detect and use it

### P1 — Add Diagnostic Capability

1. **Implement** `python -m auralis.cli doctor` command
2. **Check:**
   - yt-dlp version
   - bgutil plugin installed?
   - Provider service reachable?
   - Cookies file valid?
   - YouTube accessible?

### P2 — Test & Validate

1. **Unit tests:**
   - Cookie parsing correctness (roundtrip JSON→Netscape)
   - yt-dlp options construction
   - Error classification

2. **Integration tests:**
   - Mock YouTube responses (bot verification, success)
   - Mock bgutil provider
   - Test acquisition flow end-to-end

3. **Manual tests** (optional live):
   - Actual YouTube download with fresh cookies
   - Actual YouTube download with PO provider running
   - Both together

### P3 — Documentation

1. Update README with:
   - bgutil-ytdlp-pot-provider setup
   - Docker Compose example with provider sidecar
   - Cookie export instructions
   - Troubleshooting guide
   - `doctor` command usage

---

## NEXT STEPS

**Do you want me to:**

1. ✅ **Implement the fixes** (remove broken code, add doctor command, update dependencies)?
2. ✅ **Create proper docker-compose.yml** with bgutil provider?
3. ✅ **Write integration tests** that mock the correct flow?
4. ✅ **All of the above**?

The goal is to move from a "fake working" system to a **honestly failing** system that clearly tells you:
- Which exact layer is broken
- What needs to be installed/configured
- How to verify each step
- What YouTube/browser conditions might still block you

I will NOT claim "95% success rate" until we actually test it end-to-end.

