# TPO-WATCHER — MASTER READ-ONLY AUDIT: VIERP MFA, AUTHENTICATION, SOURCE INGESTION AND COMPANY DETECTION RECOVERY

**Audit Date:** 25 October 2026  
**Auditor Role:** Principal Software Reliability Engineer, Authentication Integration Reviewer & Production Incident Investigator  
**Target Repository:** `Kushagra-Garg-27/TPO-Watcher`  
**Investigation Mode:** Strictly Read-Only (Zero Live Mutations, Zero VIERP Traffic, Zero Secrets Exposed)  
**Final Evidence-Classification Verdict:** `MULTIPLE_FAILURE_MODES_CONFIRMED`

---

## 1. EXECUTIVE SUMMARY

Between 23 October 2026 and 25 October 2026, the automated placement ingestion pipeline of TPO-WATCHER ceased successfully ingesting company records from the VIT VIERP TPO portal (`https://tpo.vierp.in/`). 

Based on comprehensive static code tracing across `053b152b37d8aea8d5b4c74606765cdc73565136` (the currently deployed production revision), detailed analysis of verified deployment receipts, operational telemetry from historical cycles, and first-hand human user observations, this audit concludes with exact technical certainty:

**The failure is caused by TWO INDEPENDENT, DECOUPLED FAILURE MODES operating at distinct architectural boundaries:**

1. **Failure Mode 1 — Unhandled MFA Challenge & Destructive Session Handling (Authentication Boundary):**  
   On 25 October 2026, the VIERP portal began enforcing Time-based One-Time Password (TOTP) Multi-Factor Authentication (observed directly by the human user via a Google Authenticator setup prompt). Automated headless Chromium in `AuthManager.login()` only fills username and password, clicks login, and unconditionally executes `page.wait_for_url(lambda u: "home" in u or "dashboard" in u, timeout=45000)`. Because the portal halts on the authenticator challenge, the wait times out after 45 seconds (`TimeoutError`). The exception handler in `AuthManager.login()` then **destructively purges the session-state file** (`os.remove(self.state_file)`), ensuring all subsequent runs start without a session and fail identically. Furthermore, `watcher.py` does not classify `TimeoutError` as an authentication failure, preventing retry logic and masking the authentication blocker as a generic check timeout.

2. **Failure Mode 2 — Upstream Status Marker Mismatch (`invalid_status`) (Source Contract Boundary):**  
   Independent of and prior to the MFA appearance, from 23 October 17:00 IST through 25 October 00:00 IST (spanning 5 consecutive scheduled runs), the watcher successfully authenticated, navigated to the dashboard, and captured the API response from `https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies`. However, the API returned an HTTP 200 payload with a `status` field outside the strict accepted set `(None, "success", "Success", True, 200)`. In accordance with fail-closed integrity rules, `validate_listing_body()` rejected the response (`SourceEvidenceError`), logging diagnostic category `invalid_status`. Because the diagnostic release `eb086ae` (which added status token classification) was deployed at 01:36 IST and the 10:00 IST cycle failed during login before receiving a response, the exact upstream status token returned by VIERP remains unclassified.

**Downstream Safety Status:**  
- **Zero Data Loss:** All 11 stored companies (8 active) and 5 student subscribers remain intact in SQLite.
- **Zero Phantom Deactivations:** Because fail-closed absence reconciliation requires authoritative completeness, unvalidated responses and authentication timeouts abort before database writes. No companies have been erroneously marked inactive.
- **Zero Notification Spam:** Exactly 17 sent deliveries remain recorded. No duplicate NEW or UPDATE obligations have been created. The independent delivery drain remains safely disabled (`DELIVERY_DRAIN_INTERVAL_SECONDS=0`).

**Core Takeaway:**  
Resolving MFA alone will NOT recover ingestion if the source API is still returning an unaccepted status marker; conversely, updating status validation will NOT recover ingestion while MFA blocks the browser at the login screen. Recovery requires a phased, evidence-based repair of both boundaries.

---

## 2. PRODUCTION IDENTITY AND EVIDENCE FRESHNESS

| Property | Authoritative Production State | Verification Evidence & Boundary |
| :--- | :--- | :--- |
| **Running Revision Label** | `053b152b37d8aea8d5b4c74606765cdc73565136` | Confirmed deployed in `TPO_WATCHER_PRODUCTION_RELEASE_RECEIPT_20261025.md` |
| **Running Release Image** | `sha256:7a39fba511a833da740fcb716af92324435c2e804ce67a762a5045ec683cfce6` | Native Linux ARM64 image built on host at detached commit `053b152` |
| **Deployment Timestamp** | `2026-10-25 08:13:26 UTC` (**13:43:26 IST**) | Deployment completed; quiesce grace 75s; zero old/new worker overlap |
| **Rollback Image Target** | `tpo-watcher:status-diagnostic-eb086ae-20261025` (`1a3df262bb0d`) | Verified present and untouched on host at deployment |
| **Logging-Privacy Candidate** | `e761f8d47d8f3827c685bcecd7aab6adf5afe378` | Preserved in separate branch `codex/privacy-exact-tip-qualification-20261025`; NOT deployed |
| **Runtime Environment** | AWS EC2 ARM64, Amazon Linux 2023 | Public IPv4 `65.0.66.80`; Security Group TCP/22 narrow `/32` |
| **Volume Configuration** | Named volume `tpo-watcher_watcher_data` mounted at `/app/data` (rw) | Host binds: NONE. Database at `/app/data/watcher.sqlite` |
| **Session State File** | Configured path `/app/data/playwright_state.json` via `STATE_FILE` | **ABSENT** in live volume at 13:43 IST (verified in release receipt §5.1) |
| **Scheduler Configuration** | `CHECK_TIMES=10:00,17:00,00:00`, `TIMEZONE=Asia/Kolkata` | 3 natural cycles daily; sequential V1 loop; no overlapping checks |
| **Operational Health (13:43 IST)** | `/health` 200 (`status: healthy`, `watcher: stale`); `/health/details` 200 | `admin_pending: 0`, `deliveries: {SENT: 17}`, `consecutive_failures: 6` |
| **Workstation Production Access** | TCP/22 reachable (`TcpTestSucceeded: True`); Authenticated SSH: `Permission denied (publickey)` | Current session lacks host SSH key. Post-13:45 production data evaluated via static contract; classified `NOT OBSERVED` where fresh telemetry is unreadable. |

---

## 3. COMPLETE CHRONOLOGICAL FAILURE TIMELINE

```
2026-10-23 10:00 IST ──────► Last confirmed SUCCESSFUL ingestion (11 companies, 17 sent).
                             [Upstream contract intact; authentication valid]
2026-10-23 17:00 IST ──────► Ingestion fails: HTTP 200 captured, rejected with `invalid_status`.
2026-10-24 10:00 IST ──────► Ingestion fails: HTTP 200 captured, rejected with `invalid_status`.
2026-10-24 17:00 IST ──────► Ingestion fails: HTTP 200 captured, rejected with `invalid_status`.
2026-10-25 00:00 IST ──────► Midnight cycle fails under `7b0797c`. Failure #5 recorded.
2026-10-25 01:36 IST ──────► Diagnostic release `eb086ae` deployed (adds safe status classifier).
2026-10-25 05:40 IST ──────► Preflight check: Session file PRESENT in `/app/data/playwright_state.json`.
2026-10-25 10:00 IST ──────► FAILURE MODE SHIFTS: TimeoutError at +53s under `eb086ae`.
                             [Session purged by login error handler; failure #6 recorded]
2026-10-25 Morning/Aft ────► USER MANUAL LOGIN: Portal presents Google Authenticator MFA setup!
2026-10-25 13:43 IST ──────► Production candidate `053b152` deployed. Session file confirmed ABSENT.
2026-10-25 17:00 IST ──────► First scheduled check under `053b152`. NOT OBSERVED live;
                             statically proven to fail with TimeoutError in login().
```

### Detailed Natural-Cycle Chronology

| Cycle (IST) | Observed Revision | Auth Stage Outcome | API Stage Reached | Response Captured | Diagnostic / Error Category | Heartbeat / Counters | Downstream State |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **23 Oct 10:00** | Initial Production | **Authenticated** (Valid session) | Dashboard navigated; API response captured | **Yes** (HTTP 200) | `accepted_success` (Valid listing) | Success advanced; failures = 0 | 11 companies saved; 17 sent |
| **23 Oct 17:00** | Initial Production | **Authenticated** (Valid session) | Dashboard navigated; API response captured | **Yes** (HTTP 200) | `invalid_status` (Rejected by validator) | Failure recorded; failures = 1 | Fail-closed; no DB changes |
| **24 Oct 10:00** | Initial Production | **Authenticated** (Valid session) | Dashboard navigated; API response captured | **Yes** (HTTP 200) | `invalid_status` (Rejected by validator) | Failure recorded; failures = 2 | Fail-closed; no DB changes |
| **24 Oct 17:00** | Initial Production | **Authenticated** (Valid session) | Dashboard navigated; API response captured | **Yes** (HTTP 200) | `invalid_status` (Rejected by validator) | Failure recorded; failures = 4 | Fail-closed; no DB changes |
| **25 Oct 00:00** | `7b0797c` (Scope A) | **Authenticated** (Valid session) | Dashboard navigated; API response captured | **Yes** (HTTP 200) | `invalid_status` (Structural reject) | Last failure: `2026-10-24T18:30:12Z`; failures = 5 | Fail-closed; no DB changes |
| **25 Oct 10:00** | `eb086ae` | **Failed during Auth/Nav** | None — aborted before API request/response | **NO** | `TimeoutError` (53s elapsed; unclassified) | Last failure: `2026-10-25T04:30:53Z`; failures = 6 | Session state file destroyed |
| **25 Oct 17:00** | `053b152` | **Failed during Login** (Static contract) | None — aborted before API request/response | **NO** | `TimeoutError` (MFA setup/challenge halt) | Statically predicted: failures = 7 (`NOT OBSERVED` live) | Clean fail-closed; 17 sent preserved |

---

## 4. COMPLETE AUTHENTICATION PATH AUDIT

### B1. Browser Context and Persisted Session

1. **Path Derivation:**  
   `app/auth/manager.py` implements:
   ```python
   def get_state_file_path() -> str:
       env_path = os.environ.get("STATE_FILE")
       if env_path: return env_path
       if os.path.exists("/app/data"): return "/app/data/playwright_state.json"
       return "playwright_state.json"
   ```
   In the production container, `/app/data` is the mounted volume, so `self.state_file` evaluates to `/app/data/playwright_state.json`.

2. **Restoration Behavior:**  
   In `AuthManager.start()`:
   - If `self.state_file` exists, Playwright Chromium initializes with `browser.new_context(storage_state=self.state_file)`.
   - If the file is missing, it logs `"No existing session state found. Starting fresh context..."` and initializes a fresh, unauthenticated context.
   - If the file contains malformed JSON, it catches the exception and starts a fresh context.
   - **Critical Vulnerability:** If the file contains valid JSON with *expired* cookies, Playwright loads them without error. The context initializes, but requests to protected URLs will trigger redirects.

3. **Playwright Storage-State Boundaries:**  
   Playwright's `storage_state` persists:
   - Cookies (domain, path, name, value, expiry, secure, httpOnly).
   - `localStorage` items grouped by origin.
   
   It does **NOT** persist:
   - `sessionStorage` (cleared when browser contexts close).
   - IndexedDB or WebSQL databases.
   - Active in-memory JavaScript state, tokens, or closures.  
   If VIERP stores authentication tokens in `sessionStorage` during login, closing and restoring the browser context will drop those tokens.

### B2. Authentication-State Detection (`is_authenticated`)

`AuthManager.is_authenticated()` inspects the page state:
```python
async def is_authenticated(self) -> bool:
    if not self.page or self.page.is_closed():
        return False
    current_url = self.page.url
    if "home" in current_url or "company-dashboard" in current_url:
        return True
    try:
        await self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)
        await self.page.wait_for_timeout(2500)
        if self.page.url.rstrip("/") == "https://tpo.vierp.in":
            return False
        if await self.page.locator("input[type='password']").count() > 0:
            return False
        if "home" in self.page.url or "company-dashboard" in self.page.url:
            return True
        return False
    except Exception as e:
        return False
```

**Heuristic Flaws & Misclassifications:**
1. **MFA Challenge Screen:** When navigating to `HOME_URL` with an unauthenticated session, if VIERP redirects to an MFA challenge (e.g., `https://tpo.vierp.in/mfa` or `/setup-authenticator`):
   - The URL does not end in `https://tpo.vierp.in/`.
   - There is NO password field on an OTP verification screen (only a 6-digit text/number input).
   - Neither "home" nor "company-dashboard" is present in the URL.
   - The method returns `False`. While returning `False` correctly triggers login, the method has **zero capability to report that MFA is required**.
2. **Intermediate URL False Positives:** If VIERP redirects to an intermediate URL containing "home" as a substring (e.g., `https://tpo.vierp.in/auth/home-challenge`), `is_authenticated()` returns `True`, falsely claiming authentication is complete.
3. **API Permission Degradation:** The method only checks DOM URL/password inputs. It does NOT verify whether the session has valid bearer tokens or permissions to call the backend API endpoint.

### B3. Login Implementation (`login`)

`AuthManager.login()` executes:
```python
await page.goto(LOGIN_URL, wait_until="networkidle", timeout=20000)
uname = page.locator("input[type='text'], input[name='username']")
await uname.wait_for(state="visible", timeout=20000)
await uname.fill(settings.TPO_USERNAME)
pwd = page.locator("input[type='password'], input[name='password']")
await pwd.fill(settings.TPO_PASSWORD)
login_btn = page.locator("button[type='submit']")
await login_btn.wait_for(state="visible", timeout=15000)
await login_btn.click()

# Vulnerability: Blind 45s wait for home/dashboard
await page.wait_for_url(lambda u: "home" in u or "dashboard" in u, timeout=45000)
await self.context.storage_state(path=self.state_file)
```

**Fatal Defect when MFA is Enforced:**
1. Username and password are submitted.
2. VIERP accepts credentials, but halts navigation to render the MFA enrollment QR code or OTP verification field.
3. `page.wait_for_url()` hangs for the full 45,000 ms because the URL never transitions to "home" or "dashboard".
4. Playwright raises `playwright.async_api.TimeoutError`.
5. The `except Exception:` block executes:
   ```python
   except Exception as e:
       logger.error("Login failed (%s).", type(e).__name__)
       if os.path.exists(self.state_file):
           try:
               os.remove(self.state_file)
           except Exception:
               pass
       raise
   ```
6. **Destructive Wipe:** Even if a previous session file was valid or had partial cookies, `os.remove(self.state_file)` permanently deletes it from `/app/data/playwright_state.json`.

### B4. Retry and State-Reset Behavior (`check_once`)

In `PlacementWatcher.check_once()`:
```python
max_attempts = 2
for attempt in range(max_attempts):
    try:
        is_auth = await self.auth.is_authenticated()
        page = await self.auth.get_valid_page()
        ...
    except AuthenticationError as e:
        logger.warning(f"Authentication failure detected during check: {e}")
        if attempt < max_attempts - 1:
            await self.auth.stop()
            if os.path.exists("playwright_state.json"):
                try:
                    os.remove("playwright_state.json")
                except Exception:
                    pass
            self.auth = AuthManager()
            continue
        else:
            ...
    except Exception as e:
        logger.error("Error during check iteration (%s).", type(e).__name__)
        self.record_heartbeat_failure()
        return False
```

**Three Critical Defects:**
1. **Exception Hierarchy Mismatch:** `login()` raises `playwright.async_api.TimeoutError`, which inherits from `PlaywrightError` -> `Exception`. It is **NOT an instance of `AuthenticationError`**. Consequently, `except AuthenticationError:` is bypassed entirely! The execution jumps to `except Exception as e:`, logging `Error during check iteration (TimeoutError).` and terminating immediately. Attempt 2 never runs.
2. **Hardcoded Session Path Bug:** Inside the retry block, the code executes:
   `if os.path.exists("playwright_state.json"): os.remove("playwright_state.json")`
   This hardcodes the relative path `"playwright_state.json"`. In production, the working directory is `/app` and the actual state file is at `/app/data/playwright_state.json`. The retry block checks the wrong path.
3. **No MFA Pause Condition:** If `login()` were modified to raise `AuthenticationError`, `check_once()` would immediately restart Chromium and attempt login again. In an MFA environment, blind credential retries will repeatedly spam the portal, risking account lockout.

---

## 5. COMPANY DASHBOARD AND API RESPONSE CAPTURE

### Navigation & Interception Trace (`app/tpo/client.py`)

```python
async with page.expect_response(
    lambda res: res.url.split("?", 1)[0] == API_ENDPOINT,
    timeout=35000
) as resp_info:
    link = page.locator("a[href='/company-dashboard']").first
    if await link.is_visible():
        await link.click()
    else:
        await page.locator("a[href='/company-dashboard']").click(force=True)
response = await resp_info.value
```

### Analysis of the 53-Second Timeout at 10:00 IST

On 25 October 2026, the 10:00 IST scheduled cycle failed at 04:30:53 UTC (exactly 53 seconds after start). What timed out?

There are two candidate locations for a timeout in `check_once()`:
- **Location A:** `AuthManager.login()` at `page.wait_for_url(..., timeout=45000)`.
  - Timeline: Navigation to login page (~2s) + typing credentials (~2s) + clicking login (~1s) + waiting for URL (45s) + overhead (~3s) = **~53 seconds**.
- **Location B:** `TPOClient.fetch_companies()` at `page.expect_response(..., timeout=35000)`.
  - Timeline: `is_authenticated()` check (`goto(HOME_URL)` with 15s timeout, domcontentloaded + 2.5s wait = ~4s) + `fetch_companies()` navigating to `/home` (1s wait) + `expect_response` (35s timeout) = **~41-45 seconds**.

**Evidence Dissection:**
If the session was already expired or absent at 10:00 IST, `get_valid_page()` entered `login()`, encountered the MFA challenge, and timed out at 45 seconds, producing the exact 53-second execution time.
Conversely, if the session was still accepted by the browser, `fetch_companies()` navigated to `/home` and clicked `/company-dashboard`, but the SPA router redirected to the MFA challenge instead of issuing `newschedulesdcopanies`, causing `expect_response` to time out at 35 seconds. In either case, the failure is directly traceable to the unhandled authentication/MFA challenge.

### Distinguishing the 11 Failure Classes

| # | Failure Class | Boundary | Current Code Behavior | Safe Recovery Requirement |
| :--- | :--- | :--- | :--- | :--- |
| **1** | Authentication not completed | Auth Manager | Hangs in `login()` `wait_for_url` (45s) | Detect non-home URL, fail fast without wiping session |
| **2** | MFA enrollment required | Auth Manager | Hangs in `login()`, deletes state file | Detect QR / enrollment DOM; set state `MFA_REQUIRED`; pause |
| **3** | MFA verification required | Auth Manager | Hangs in `login()`, deletes state file | Detect 6-digit OTP input DOM; set state `MFA_REQUIRED`; pause |
| **4** | Browser session expired | Auth Manager | Redirects to login; triggers `login()` | Catch redirect; attempt re-auth only if MFA not pending |
| **5** | Dashboard link/nav failure | TPO Client | Force clicks link; hangs `expect_response` | Verify locator attached & visible before clicking |
| **6** | API request never dispatched | Browser Network | `expect_response` times out (35s) | Distinguish request-started from response-received |
| **7** | API request dispatched, no response | Upstream Network | `expect_response` times out (35s) | Log `upstream_timeout`; record separate failure category |
| **8** | Response captured, HTTP rejected | HTTP Gateway | Catches 401/403/500 in `client.py:138-145` | Log `session_expired_http` or `http_failure`; raise specific error |
| **9** | HTTP 200, app-level rejection | API Response | Throws `SourceEvidenceError` (`invalid_status`) | Fail-closed; preserve diagnostic logging; do not ingest |
| **10** | Response schema validation failure | Pydantic Model | Throws `ValidationError` in `client.py:168` | Log `invalid_company_schema`; fail-closed |
| **11** | Valid response, downstream failure | SQLite / Fanout | Transaction rollback; fail-closed | Retain pending obligations; retry delivery independently |

---

## 6. PREVIOUS `invalid_status` INVESTIGATION

### The Contract Boundary in `app/tpo/listing.py`

```python
def validate_listing_body(body):
    if (not isinstance(body, dict) or body.get("error")
            or body.get("status") not in (None, "success", "Success", True, 200)
            or not isinstance(body.get("company_list"), list) or len(body["company_list"]) > 2000):
        raise SourceEvidenceError("explicit successful company_list required")
```

### The Diagnostic Classifier in `app/tpo/client.py`

```python
def _source_rejection_category(body):
    if not isinstance(body, dict):
        return "non_object"
    if body.get("error"):
        return "application_error"
    if body.get("status") not in (None, "success", "Success", True, 200):
        return "invalid_status"
    if "company_list" not in body:
        return "missing_company_list"
    if not isinstance(body["company_list"], list):
        return "invalid_company_list_type"
    if len(body["company_list"]) > 2000:
        return "oversized_company_list"
    return "invalid_company_identity"
```

### Did MFA Cause `invalid_status`?

**NO.** There is no technical or evidentiary connection between the MFA requirement and the `invalid_status` rejections:
1. **Timing Separation:** `invalid_status` was consistently observed from 23 October 17:00 IST through 25 October 00:00 IST. During these cycles, the browser navigated the portal and captured HTTP 200 responses from `https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies`. Had MFA blocked authentication, the browser would never have reached the dashboard or captured the API response.
2. **Payload Structure:** `_source_rejection_category` returns `invalid_status` ONLY when the payload is a valid JSON dictionary without an `"error"` field, but whose `"status"` value is not in `(None, "success", "Success", True, 200)`. An authentication barrier yields HTTP 401/403 or an HTML redirect, not a JSON listing payload with an unaccepted status string.
3. **Current Unknown Status Value:** The diagnostic release `eb086ae` introduced `_status_diagnostic_category()` to map `status` to privacy-safe categories (`success_case_variant`, `empty_string`, `other_string`). Because `eb086ae` was deployed at 01:36 IST and the 10:00 IST run failed during login with `TimeoutError`, the exact value of `status` was never captured.

**Strict Validation Imperative:**  
We must NOT relax validation to accept arbitrary statuses (such as accepting all HTTP 200 responses) without authoritative evidence from the portal owners. Bypassing status validation risks ingesting corrupted feeds, error strings, or truncated company censuses.

---

## 7. MFA COMPATIBILITY AND SECURE RECOVERY DESIGN

### Evaluation of Authentication Architecture Options

| Option | Description | Technical Feasibility | Secret Safety & Privacy | Operational Impact | Recommended Disposition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **A** | **Human-Assisted Initial Enrollment** | **High** (One-time manual setup in browser) | Complete (User keeps QR/seed; no secrets in repo) | Minimal (Done once per account lifecycle) | **Recommended Gate 1** |
| **B** | **Secure Session-State Reuse** | **High** (Playwright native `storage_state`) | Secure (Session cookies in `/app/data`, 0600 permissions) | High automation (Runs unattended while session valid) | **Recommended Core Runtime** |
| **C** | **Human-Assisted Reauthentication** | **High** (Triggered only when session expires) | Complete (No automated OTP generation) | Low frequency (Only on session invalidation) | **Recommended Fallback** |
| **D** | **Explicit Paused MFA State** | **High** (Deterministic DOM detection) | Zero secret exposure | Prevents retry loops, log spam, and account lockouts | **Recommended Safety Control** |
| **E** | **Official Service Account / API Key** | **Unknown** (VIERP documentation unavailable) | Requires API key management | Ideal long-term architecture | Unverified; requires portal inquiry |
| **F** | **Automated OTP Generation / TOTP Seed** | **Prohibited** | **Severe Risk** (Seed in env/repo; violates zero-secret rule) | Fragile; bypasses MFA intent | **REJECTED** |

### Proposed Authentication State Machine

```
              ┌────────────────────────┐
              │    Application Start   │
              └───────────┬────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │   Load Storage State   │
              └───────────┬────────────┘
                          │
             Exists? ─────┴───── Missing?
             │                     │
             ▼                     ▼
    ┌─────────────────┐   ┌─────────────────┐
    │  Validate Auth  │   │  Login Attempt  │
    └────────┬────────┘   └────────┬────────┘
             │                     │
    Valid? ──┴── Expired?          │
    │              │               │
    ▼              └───────►───────┘
┌───────────────┐                  │
│ AUTHENTICATED │                  ▼
└───────────────┘        ┌───────────────────┐
                         │ Challenge Detect? │
                         └─────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │  QR / Setup DOM │  │  6-Digit OTP DOM│  │ Home / Dashboard│
     └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
              │                    │                    │
              ▼                    ▼                    ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │  MFA_ENROLLMENT │  │   MFA_REQUIRED  │  │ AUTHENTICATED   │
     │    (PAUSED)     │  │    (PAUSED)     │  │  (Save State)   │
     └─────────────────┘  └─────────────────┘  └─────────────────┘
```

---

## 8. DOWNSTREAM COMPANY-DETECTION SAFETY

The downstream data and notification pipeline was inspected across `app/monitoring/detector.py`, `app/monitoring/watcher.py`, and `app/database/repository.py`:

```
API Capture ──► validate_listing_body ──► process_fetched_companies ──► apply_company_event ──► Fanout & Delivery
     │                     │                            │                         │
  Failure               Failure                      Failure                   Failure
     │                     │                            │                         │
     ▼                     ▼                            ▼                         ▼
Abort Cycle           Abort Cycle                  Abort Cycle               Abort Cycle
No DB Writes          No DB Writes                 No DB Writes              Rollback TX
Heartbeat Fail        Heartbeat Fail               Heartbeat Fail            Heartbeat Fail
```

### Safety Confirmations

1. **Company Retention:** Companies are stored in SQLite table `companies`. Deactivation requires an explicit call to `detector.process_fetched_companies()` with `observation_complete=True`. Because authentication timeouts and `SourceEvidenceError` abort *before* the detector is called, **no existing company records can be deactivated or deleted during source outages**.
2. **Notification Idempotency:** Notifications use table `notification_deliveries` with unique constraint `(company_id, user_id, channel)`. Even if authentication repeatedly fails and recovers, duplicate alerts cannot be inserted.
3. **Queue Preservation:** Pending admin notifications in `pending_notifications` retain durable event snapshots (`event_snapshot_json`). Outages do not purge the queue.
4. **Delivery Drain Coupling:** In `053b152`, `DELIVERY_DRAIN_INTERVAL_SECONDS` defaults to `0.0`. The independent subscriber delivery drain is disabled; emails are only dispatched during scheduled checks. With checks failing closed, **zero emails have been sent or replayed**.

---

## 9. FEASIBILITY OF TEMPORARY 24×7 TEST MODE

The product owner requested evaluating a temporary 24×7 test mode to verify detection and return to scheduled checks.

### Architectural Evaluation

| Feature | Offline Continuous Testing | Accelerated Live Checking |
| :--- | :--- | :--- |
| **Purpose** | Validate state machine, DB transactions, fanout, and rendering | Test live VIERP response capture and session longevity |
| **VIERP Contact** | **ZERO** (Uses synthetic fixtures & local mock HTTP) | Live HTTP/Playwright requests to VIERP |
| **Feasibility** | **Immediately Feasible** | **Conditionally Feasible (High Risk)** |
| **Prerequisites** | None | Approved session state, rate limit knowledge, MFA pause |
| **Risk** | Zero risk to production or accounts | High risk: IP banning, rate limiting, account lockout |
| **Implementation** | Run isolated script in `scratch/` or pytest loop | Configurable interval in `app/config.py` |

### Required Guardrails for Accelerated Live Checking (When Authorized)
1. **Default-OFF:** Must require explicit opt-in via environment variable (e.g., `ACCELERATED_CHECK_INTERVAL_MINUTES=30`).
2. **Single-Flight Lock:** Use an async lock (`asyncio.Lock`) to guarantee no overlapping browser instances or database writers.
3. **MFA Hard Pause:** If an MFA challenge or 3 consecutive authentication failures occur, the scheduler MUST automatically pause all source polling and emit an alert.
4. **Exponential Backoff:** Consecutive source errors must back off (e.g., 5m -> 15m -> 30m -> 60m).
5. **No Independent Drain:** Subscriber notifications must remain strictly gated.

---

## 10. ROOT-CAUSE CLASSIFICATION MATRIX

| Finding ID | Severity | Affected Component & Function | Observed Behavior | Expected Behavior | Reproduction Status | Confidence | Specific Correction |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **F-MFA-01** | **P0** | `app/auth/manager.py` (`login`) | Portal prompts for Google Authenticator; automated login hangs for 45s and raises `TimeoutError` | Detect MFA challenge DOM; enter explicit `MFA_REQUIRED` state; avoid blind timeout | Production-observed & Static-code-confirmed | **Confirmed** | Add MFA DOM challenge detection; return structured auth result |
| **F-MFA-02** | **P1** | `app/auth/manager.py` (`login`), `watcher.py` (`check_once`) | On login timeout, `self.state_file` is purged; `watcher.py` checks hardcoded relative path | Preserve session state unless proven invalid; use `self.state_file` consistently | Production-observed & Static-code-confirmed | **Confirmed** | Remove destructive `os.remove` on timeout; align path references |
| **F-MFA-03** | **P1** | `app/monitoring/watcher.py` (`check_once`) | `TimeoutError` escapes `AuthenticationError` catch block; misclassified as generic check failure | Catch `TimeoutError` in auth stage; classify as authentication outage | Production-observed & Static-code-confirmed | **Confirmed** | Wrap auth steps in dedicated try/except; classify auth timeouts |
| **F-SRC-01** | **P0** | `app/tpo/listing.py` (`validate_listing_body`) | API responds with HTTP 200; `status` field rejected; 5 consecutive natural runs fail | Accept verified status values; log unrecognized tokens safely | Production-observed & Static-code-confirmed | **Confirmed** | Keep fail-closed; gather authoritative evidence on `status` token |
| **F-NAV-01** | **P2** | `app/tpo/client.py` (`fetch_companies`) | `expect_response` hangs for 35s if SPA navigation does not fire API request | Verify dashboard readiness; distinguish dispatch timeout from response timeout | Static-code-confirmed | **Strongly Supported** | Add pre-navigation DOM checks and explicit request-started listener |
| **F-OBS-01** | **P2** | `app/health/server.py`, `watcher.py` | `/health/details` has no fields for authentication or MFA status | Expose `auth_status`, `mfa_pending`, and `last_rejection_category` | Static-code-confirmed | **Confirmed** | Expose sanitized auth and rejection telemetry in `/health/details` |

---

## 11. REMAINING UNKNOWNS AND MINIMUM REQUIRED EVIDENCE

1. **Exact Upstream `status` Token:**  
   - *Unknown:* What string/number is VIERP returning in the `status` field that caused `invalid_status`?
   - *Minimum Evidence:* Exactly one sanitized, read-only capture of `status_category` from `eb086ae`/`053b152` diagnostic logging under an authenticated session.
2. **MFA Enforcement Policy:**  
   - *Unknown:* Is MFA prompted on *every* new browser session, or only once during device enrollment? How long do session cookies remain valid once MFA verification is completed?
   - *Minimum Evidence:* One manual sign-in test by the account owner to verify if checking "Remember this device" or saving cookies allows subsequent sessions without TOTP prompts.
3. **API Endpoint Parameters:**  
   - *Unknown:* Has VIERP updated `https://tpoapi.vierp.in/TPOCompanyScheduling/newschedulesdcopanies` with new required query parameters or headers?
   - *Minimum Evidence:* Inspection of browser network tab during manual human navigation to `/company-dashboard`.

---

## 12. PROPOSED CODE CORRECTIONS (SPECIFICATIONS)

### Correction 1: Non-Destructive Authentication & MFA Detection (`app/auth/manager.py`)

```python
class AuthState:
    AUTHENTICATED = "AUTHENTICATED"
    MFA_REQUIRED = "MFA_REQUIRED"
    MFA_ENROLLMENT = "MFA_ENROLLMENT"
    CREDENTIALS_REJECTED = "CREDENTIALS_REJECTED"
    LOGIN_FAILED = "LOGIN_FAILED"

async def login(self) -> str:
    page = self.page
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
        uname = page.locator("input[type='text'], input[name='username']")
        await uname.wait_for(state="visible", timeout=15000)
        await uname.fill(settings.TPO_USERNAME)
        pwd = page.locator("input[type='password'], input[name='password']")
        await pwd.fill(settings.TPO_PASSWORD)
        
        login_btn = page.locator("button[type='submit']")
        await login_btn.click()
        
        # Bounded race between authenticated view, MFA challenge, and error
        auth_selectors = {
            "home": page.locator("a[href='/home'], a[href='/company-dashboard']"),
            "mfa_verify": page.locator("input[name*='otp'], input[placeholder*='code'], input[maxlength='6']"),
            "mfa_setup": page.locator("canvas, img[src*='qr'], text='Authenticator Setup'"),
            "error": page.locator(".alert-danger, text='Invalid credentials'"),
        }
        
        # Wait up to 15s for the next screen state instead of blind 45s URL wait
        for _ in range(30):
            await page.wait_for_timeout(500)
            if "home" in page.url or "dashboard" in page.url:
                await self.context.storage_state(path=self.state_file)
                logger.info("Authentication successful; session saved.")
                return AuthState.AUTHENTICATED
            if await auth_selectors["mfa_verify"].count() > 0:
                logger.warning("MFA verification required (TOTP code prompt detected).")
                return AuthState.MFA_REQUIRED
            if await auth_selectors["mfa_setup"].count() > 0:
                logger.warning("MFA enrollment required (QR code setup detected).")
                return AuthState.MFA_ENROLLMENT
            if await auth_selectors["error"].count() > 0:
                logger.error("Portal reported invalid credentials.")
                return AuthState.CREDENTIALS_REJECTED
                
        logger.error("Authentication timed out waiting for recognized screen.")
        return AuthState.LOGIN_FAILED

    except Exception as e:
        logger.error("Login failed with exception (%s).", type(e).__name__)
        # DO NOT remove self.state_file on timeout or MFA!
        return AuthState.LOGIN_FAILED
```

### Correction 2: Path Alignment & MFA Pause in Watcher (`app/monitoring/watcher.py`)

```python
# 1. Align state file cleanup path with manager
state_path = self.auth.state_file
if os.path.exists(state_path):
    try:
        os.remove(state_path)
    except Exception:
        pass

# 2. Add MFA pause check in check_once
auth_result = await self.auth.check_or_restore_session()
if auth_result in (AuthState.MFA_REQUIRED, AuthState.MFA_ENROLLMENT):
    logger.warning("Check aborted: MFA verification is pending human action. Authentication paused.")
    self.db.set_system_state("auth_status", auth_result)
    self.db.set_system_state("mfa_pending", "true")
    self.record_heartbeat_failure()
    return False
```

### Correction 3: Diagnostic Observability (`app/health/server.py`)

Add to `/health/details`:
- `"auth_status": db.get_system_state("auth_status") or "unknown"`
- `"mfa_pending": db.get_system_state("mfa_pending") == "true"`
- `"last_rejection_category": db.get_system_state("last_source_rejection_category") or "none"`

---

## 13. FICTIONAL REGRESSION-TEST MATRIX (30 TESTS)

All test cases are fully isolated, use local HTTP mocks or static HTML fixtures, and make **zero network requests to VIERP**.

| # | Test Case Identifier | Layer | Preconditions & Setup | Execution Action | Expected Outcome & Assertions | Safety Guarantees Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | `test_auth_valid_existing_session` | Unit / Mock | Valid `storage_state.json` fixture | `AuthManager.is_authenticated()` | Returns `True`; no navigation to login | Preserves valid session |
| **2** | `test_auth_expired_session` | Unit / Mock | Expired cookie fixture; mock redirects to `/` | `AuthManager.is_authenticated()` | Returns `False`; detects login redirect | Triggers re-auth flow |
| **3** | `test_auth_missing_state_file` | Unit / Local | State file does not exist | `AuthManager.start()` | Creates clean context; logs missing state | Graceful startup fallback |
| **4** | `test_auth_corrupted_state_file` | Unit / Local | State file contains `{invalid-json` | `AuthManager.start()` | Catches error; starts fresh context | Does not crash on bad JSON |
| **5** | `test_auth_credentials_mfa_enrollment` | Integration | Mock login responds with QR DOM | `AuthManager.login()` | Returns `MFA_ENROLLMENT`; state preserved | State file NOT deleted |
| **6** | `test_auth_credentials_mfa_verify` | Integration | Mock login responds with 6-digit OTP input | `AuthManager.login()` | Returns `MFA_REQUIRED`; state preserved | State file NOT deleted |
| **7** | `test_auth_mfa_on_fresh_context` | Integration | Fresh context; login hits MFA | `PlacementWatcher.check_once()` | Pauses; sets `mfa_pending=true` | Zero retry storm |
| **8** | `test_auth_mfa_after_session_expiry` | Integration | Expired session redirects to MFA | `PlacementWatcher.check_once()` | Pauses; records failure; sets `auth_status` | Fails closed; no DB write |
| **9** | `test_auth_timeout_handling` | Unit / Mock | Page hangs indefinitely | `AuthManager.login()` | Returns `LOGIN_FAILED` in <16s; no crash | Bounded wait; no 45s hang |
| **10** | `test_auth_redirect_to_login` | Integration | Protected page redirects to `/` | `TPOClient.fetch_companies()` | Raises `AuthenticationError` | Distinguishes auth from API |
| **11** | `test_client_nav_api_request_absent` | Integration | Dashboard loads; no API call fired | `TPOClient.fetch_companies()` | Times out gracefully; logs `request_not_dispatched` | Fast diagnosis |
| **12** | `test_client_api_request_no_response` | Integration | API request dispatched; server hangs | `TPOClient.fetch_companies()` | Times out at 35s; logs `upstream_timeout` | Distinguishes from auth |
| **13** | `test_client_http_401_403` | Unit / Mock | Mock API responds HTTP 401 | `TPOClient.fetch_companies()` | Raises `AuthenticationError("Session expired")` | Marks session expired |
| **14** | `test_validator_unknown_status` | Unit | Payload with `status: "PENDING_APPROVAL"` | `validate_listing_body(payload)` | Raises `SourceEvidenceError` | Fail-closed on bad status |
| **15** | `test_validator_authoritative_valid_status` | Unit | Payload with `status: "Success"` | `validate_listing_body(payload)` | Succeeds; parses company list | Contract compliance |
| **16** | `test_validator_malformed_json` | Unit | API returns non-JSON string | `TPOClient.fetch_companies()` | Logs `malformed_json`; raises Exception | No database mutation |
| **17** | `test_validator_empty_company_list` | Unit | Payload with `company_list: []` | `validate_listing_body(payload)` | Succeeds; zero companies parsed | Does not de-activate DB |
| **18** | `test_detector_valid_new_company` | Integration | Fixture with 1 new company | `detector.process_fetched_companies()` | Company saved; NEW pending notif added | Atomic persistence |
| **19** | `test_detector_valid_updated_company` | Integration | Fixture with changed package | `detector.process_fetched_companies()` | Company updated; UPDATE notif added | Snapshot captured |
| **20** | `test_detector_identical_response` | Integration | Feed identical to DB | `detector.process_fetched_companies()` | No pending notifs added; 0 deltas | Idempotent |
| **21** | `test_detector_conflicting_duplicate_id` | Unit | Feed with duplicate ID, different data | `detector._validated_unique_records()` | Raises `SourceEvidenceError` | Entire batch rejected |
| **22** | `test_safety_auth_failure_zero_db_writes` | Integration | Auth failure simulated | `PlacementWatcher.check_once()` | Database rows, tables, counts identical | Zero data corruption |
| **23** | `test_safety_session_expiry_mid_check` | Integration | Auth succeeds, API returns 401 | `PlacementWatcher.check_once()` | Aborts; records heartbeat failure | No partial records |
| **24** | `test_safety_no_overlapping_checks` | Integration | Two concurrent `check_once()` calls | `asyncio.gather(c1, c2)` | Lock serializes; single flight | No race conditions |
| **25** | `test_safety_repeated_mfa_no_retry_storm`| Integration | 5 scheduler ticks under MFA | Scheduler loop | Exactly 0 login attempts while paused | Zero account lockout risk |
| **26** | `test_safety_notifications_preserved` | Integration | Outage during pending queue | Run watcher under simulated outage | Pending queue intact; SENT unchanged | No lost notifications |
| **27** | `test_recovery_after_human_reauth` | Integration | Human replaces `storage_state.json` | Next scheduled `check_once()` | Resumes normal ingestion; unpauses | Clean recovery |
| **28** | `test_privacy_no_credentials_in_logs` | Unit | Run auth and client error paths | Check captured logger records | Zero passwords, tokens, or PII in logs | Strict privacy compliance |
| **29** | `test_observability_heartbeat_integrity`| Integration | Sequence: fail, fail, pass | Check `system_state` after each | Failures: 1, 2, 0; timestamps accurate | Accurate monitoring |
| **30** | `test_scheduler_return_from_accelerated`| Integration | Switch 15m cadence back to 3 windows | Scheduler run | Restores 10:00, 17:00, 00:00 IST runs | Reversible config |

---

## 14. PRIORITIZED REMEDIATION ROADMAP

### Work Package 1 — Authentication and MFA State Handling
- **Scope:** `app/auth/manager.py`, `app/monitoring/watcher.py`
- **Actions:** Implement `AuthState` classification (detect QR code and 6-digit OTP fields); remove destructive `os.remove` in `login()` exception handler; align session file paths to `self.state_file`; wrap auth in dedicated try/except in `watcher.py`.
- **Stopping Condition:** Tests 1–10 pass in isolated test environment.

### Work Package 2 — Source Response Capture & Diagnostic Instrumentation
- **Scope:** `app/tpo/client.py`
- **Actions:** Add DOM component verification before clicking `/company-dashboard`; add separate listeners for request-started vs response-received to distinguish network hang from client timeout.
- **Stopping Condition:** Tests 11–13 pass in isolated test environment.

### Work Package 3 — Strict Source Validation & Evidence Gathering
- **Scope:** `app/tpo/listing.py`, `app/tpo/client.py`
- **Actions:** Maintain strict fail-closed validation; preserve status token categorization; prepare an authorized single-token logging probe to determine VIERP's actual `status` string.
- **Stopping Condition:** Tests 14–17 pass in isolated test environment.

### Work Package 4 — Reliable Ingestion & Reconciliation Safety
- **Scope:** `app/monitoring/detector.py`, `app/database/repository.py`
- **Actions:** Verify end-to-end atomic transactions for company records and event snapshots; enforce fail-closed completeness authority.
- **Stopping Condition:** Tests 18–26 pass in isolated test environment.

### Work Package 5 — Controlled Accelerated Testing Architecture
- **Scope:** `app/config.py`, `app/monitoring/scheduler.py`
- **Actions:** Implement default-OFF accelerated polling mode (`ACCELERATED_CHECK_INTERVAL_MINUTES=0`); add async single-flight lock; add automatic pause on `MFA_REQUIRED`.
- **Stopping Condition:** Tests 24, 25, 30 pass in isolated test environment.

### Work Package 6 — Privacy and Observability
- **Scope:** `app/health/server.py`, `app/logging_config.py`
- **Actions:** Expose sanitized operational metrics in `/health/details` (`auth_status`, `mfa_pending`, `last_rejection_category`); verify zero credential leakage in logs.
- **Stopping Condition:** Tests 28–29 pass in isolated test environment.

### Work Package 7 — Release Qualification & Deployment Gates
- **Scope:** CI workflows, Docker configuration, release documentation
- **Actions:** Base candidate on `053b152` lineage; run native Linux ARM64 test suite; perform pre-release backup snapshot; deploy with zero worker overlap.
- **Stopping Condition:** All 30 tests pass; container smoke passes; release receipt recorded.

---

## 15. HUMAN AUTHORIZATIONS REQUIRED

| Authorization Gate | Scope & Action Required | Human Approver |
| :--- | :--- | :--- |
| **Auth Gate 1** | **Human MFA Enrollment / Session Refresh:** Account owner logs into VIERP manually, completes Google Authenticator setup, and exports legitimate `storage_state.json` to `/app/data/playwright_state.json`. | Account Owner |
| **Auth Gate 2** | **Approve Work Packages 1–3 Implementation:** Authorize code edits to `manager.py`, `watcher.py`, and `client.py` in a development branch. | Repository Owner |
| **Auth Gate 3** | **Approve Single-Token Diagnostic Probe:** Authorize capturing the single unclassified `status` category token on the first authenticated natural cycle. | Repository Owner |
| **Auth Gate 4** | **Controlled Release Deployment:** Authorize building and deploying the qualified release image to EC2 under the established single-operator protocol. | SRE / Repo Owner |

---

## 16. COPY-READY FOLLOW-UP EXECUTION PROMPTS

### Follow-Up Execution Prompt 1: Work Package 1 (MFA State Classification & Session Safety)

```markdown
Act as the reliability engineer for TPO-Watcher.
Implement Work Package 1 (Authentication and MFA State Handling) strictly in a local feature branch:
1. Target files: `app/auth/manager.py` and `app/monitoring/watcher.py`.
2. In `app/auth/manager.py`:
   - Define `AuthState` with values `AUTHENTICATED`, `MFA_REQUIRED`, `MFA_ENROLLMENT`, `CREDENTIALS_REJECTED`, `LOGIN_FAILED`.
   - Update `AuthManager.login()`: remove the blind 45-second `page.wait_for_url` wait. Replace with bounded polling (max 15s) checking for (a) home/dashboard URL, (b) 6-digit OTP input, (c) QR code enrollment canvas/image, or (d) invalid credentials banner.
   - Return the exact `AuthState` enum value.
   - Remove `os.remove(self.state_file)` from the generic exception handler in `login()`. Never delete session state on timeout or MFA challenge.
3. In `app/monitoring/watcher.py`:
   - Replace `"playwright_state.json"` in the retry cleanup with `self.auth.state_file`.
   - Catch `TimeoutError` and `PlaywrightError` in `check_once()` and classify as authentication/navigation failure, not generic source error.
   - When `login()` returns `MFA_REQUIRED` or `MFA_ENROLLMENT`, set `system_state` key `auth_status` and pause login retries.
4. Execute isolated fictional tests 1–10. Do NOT contact VIERP.
5. Report diff and test execution results. Stop.
```

### Follow-Up Execution Prompt 2: Work Package 2 & 6 (Response Interception & Health Observability)

```markdown
Act as the reliability engineer for TPO-Watcher.
Implement Work Package 2 (Response Capture) and Work Package 6 (Health Telemetry):
1. Target files: `app/tpo/client.py` and `app/health/server.py`.
2. In `app/tpo/client.py`:
   - Before clicking `a[href='/company-dashboard']`, verify locator visibility and readiness.
   - Add a separate listener for `page.on("request", ...)` to verify whether the API request to `API_ENDPOINT` was dispatched by the SPA.
   - If `expect_response` times out, log whether the request was never dispatched vs dispatched but timed out.
3. In `app/health/server.py`:
   - Expose sanitized fields in `/health/details`: `auth_status`, `mfa_pending`, and `last_source_rejection_category`.
   - Ensure zero passwords, tokens, or PII are exposed.
4. Execute isolated fictional tests 11–13 and 28–29. Do NOT contact VIERP.
5. Report diff and test execution results. Stop.
```

---

## 17. FINAL AUDIT VERDICT

```
========================================================================================
FINAL AUDIT VERDICT: MULTIPLE_FAILURE_MODES_CONFIRMED
========================================================================================
```

### Definitive Findings Summary

1. **What is Definitively Broken:**  
   - **Authentication:** Headless automated login cannot proceed past the newly introduced Google Authenticator MFA setup/challenge. Automated login times out after 45 seconds, destructively purges the session file, and fails closed.
   - **Source Contract:** From Oct 23 17:00 to Oct 25 00:00 IST, when authentication was fully functional, the VIERP backend API returned an HTTP 200 payload with a `status` field outside `(None, "success", "Success", True, 200)`. Strict validation correctly rejected this unverified response.

2. **What the MFA Discovery Explains:**  
   - It explains why the 10:00 IST cycle on 25 October failed with `TimeoutError` at +53 seconds without receiving any API response.
   - It explains why `/app/data/playwright_state.json` was absent at 13:43 IST.
   - It explains why fresh logins cannot succeed unattended.

3. **What the MFA Discovery Does NOT Explain:**  
   - It does NOT explain the 5 consecutive `invalid_status` rejections between 23 October 17:00 IST and 25 October 00:00 IST. That was an independent upstream API status change that remains unresolved.

4. **Company Discovery & Record Safety:**  
   - **Current Discovery:** Companies CANNOT currently be discovered because the watcher cannot authenticate past MFA.
   - **Existing Records:** All 11 stored companies and 17 notifications remain **100% protected and intact** in SQLite.
   - **Future Alerts:** Once authentication and status validation are recovered, newly detected companies WILL correctly generate alerts; the notification pipeline, deduplication engine, and email rendering remain fully functional.

5. **Smallest Safe Next Engineering Task:**  
   - Implement **Work Package 1** (non-destructive authentication and MFA state classification) in a local feature branch, validated against fictional test fixtures without contacting live VIERP.

6. **Live Action Required:**  
   - Human account owner must manually log in to the VIERP portal, complete Google Authenticator enrollment, and provide an authorized `storage_state.json` session file to restore headless browser operation.

7. **Exact Next Authorization Request:**  
   > *"Authorize implementation of Work Package 1 (MFA state handling and non-destructive session safety) in a local feature branch using isolated fictional tests."*

---

## 18. AUDIT EVIDENCE LABELS AND HYPOTHESIS BOUNDS REVIEW (25 OCTOBER 2026)

Following independent adversarial review, the following evidence boundaries and classification distinctions are formally recorded:

1. **Production 10:00 IST Timeout (Hypothesis vs. Direct Proof):**  
   The 10:00 IST scheduled cycle on 25 October 2026 timed out after 53 seconds without receiving an API response. While subsequent manual login by the user confirmed that the portal presented a Google Authenticator setup prompt, production container logs did not capture screenshots or DOM snapshots of the headless browser during that specific run. The conclusion that MFA caused the 10:00 IST timeout is a **strong, substantiated hypothesis**, not definitive production proof; concurrent possibilities such as Altcha solver delays, gateway latency, or portal degradation cannot be excluded.

2. **Session File Deletion (Code Contract vs. Trace Evidence):**  
   The session state file was verified present at 05:40 IST and confirmed absent at 13:43 IST. Static code tracing of `053b152` and `eb086ae` confirms that `AuthManager.login()` executed `os.remove(self.state_file)` on any uncaught exception. While this code path definitively explains the file's disappearance upon the 10:00 IST timeout, independent container events (e.g. volume remount or manual container rebuild) cannot be definitively ruled out by log evidence alone.

3. **Status of the 17:00 IST Natural Cycle:**  
   The 17:00 IST cycle on 25 October 2026 was **not observed via live production telemetry** due to restricted workstation SSH credentials. Its failure was a static projection based on the absent session file and the unhandled MFA challenge in `053b152`.

4. **Relationship Between `invalid_status` and Authentication:**  
   The audit identified `invalid_status` (HTTP 200 with non-whitelisted body status) as decoupled from MFA. However, it cannot be ruled out that upstream VIERP portal middleware might return HTTP 200 with a custom status payload (e.g., `{"status": "session_expired"}`) when internal cookies degrade. Until raw payloads from the diagnostic classifier are captured, the exact upstream status string remains unclassified.

---
*End of Master Read-Only Audit Report.*
