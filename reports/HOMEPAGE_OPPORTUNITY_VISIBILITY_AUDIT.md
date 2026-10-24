# TPO-WATCHER — HOMEPAGE OPPORTUNITY VISIBILITY AUDIT

**Audit Date:** 23 October 2026  
**Auditor:** Antigravity AI  
**Deployed Release Target:** `0c63f44ddafc1f8e9324992646908a6d77b62d4c`  
**Host Environment:** AWS EC2 `65.0.66.80` (`t4g.small`, ap-south-1c, ARM64)  
**Public Domain:** `https://tpowatcher.duckdns.org`  
**Execution Boundary:** **STRICTLY READ-ONLY** (No remote push, no container restart, no database modifications, no test emails dispatched).  
**Investigation Priority:** High — Homepage Product Experience

---

## 1. Executive Summary

A critical user experience discrepancy was identified on the production homepage of TPO-WATCHER (`https://tpowatcher.duckdns.org`):
- **Hero Section:** The prominent *TPO Opportunities* panel rendered an empty state stating:
  ```text
  NO VERIFIED CURRENT LISTINGS
  SOURCE FRESHNESS UNCONFIRMED
  ```
- **Intelligence Archive Section:** Further down on the exact same page, Section 02 displayed five genuine, active placement opportunities from the database: Forbes Marshall, Valentius Kryptix, Eaton, and two distinct Cummins India records.

A rigorous, strictly read-only inspection of the live EC2 host, container environment, SQLite database (`watcher.sqlite?mode=ro`), and reverse proxy was conducted. The investigation conclusively confirms the root cause: an architectural integration mismatch between the deployed legacy polling engine (`PlacementWatcher`) and the strict requirements of the V2 verified-listing projection (`listing_projection`).

Normal legacy polling stores active opportunities in the `companies` table and updates heartbeat timestamps in `system_state`, but never writes a `notification:listing-contract` or per-company observation evidence (`notification:listing:*`). Consequently, `/api/v1/opportunity-listing` returned `UNVERIFIED` with zero items. Because `LiveOpportunities.tsx` in the hero exclusively queried that endpoint and suppressed any listing without fresh verified status, the hero remained blank while `RecentDetections.tsx` queried `/api/v1/opportunities` and displayed the historical database records.

---

## 2. Production Environment Telemetry

### 2.1 Commit Provenance & Container Status
- **EC2 Git Working Tree:**
  ```text
  HEAD detached at 0c63f44
  nothing to commit, working tree clean
  commit 0c63f44ddafc1f8e9324992646908a6d77b62d4c
  ```
- **Container Telemetry:**
  - Container `tpo-watcher` (`tpo-watcher-watcher`), ID: `460998d16114`, Status: `Up 45+ minutes`, Restarts: `0`.
  - Ingress Reverse Proxy `tpo-caddy` (`caddy:2`), ID: `14795540425c`, Status: `Up 11 days`.
  - Memory: 52 MB / 2 GB RAM (<3%). Clean Uvicorn event loop and TimezoneScheduler.

### 2.2 Live Public Endpoint Responses
Both endpoints were probed via HTTPS and local loopback:

#### A. `/api/v1/opportunity-listing` (Hero Target)
- **HTTP Status:** `200 OK`
- **Response Headers:** `Via: 1.1 Caddy`, `Content-Type: application/json`, `Strict-Transport-Security: max-age=31536000`
- **Payload:**
  ```json
  {
    "items": [],
    "total": 0,
    "shown": 0,
    "status": "UNVERIFIED",
    "observed_ms": null
  }
  ```

#### B. `/api/v1/opportunities` (Archive Target)
- **HTTP Status:** `200 OK`
- **Payload Shape:** JSON array containing **5 active records**:
  1. `id: "5988"`, `company: "Forbes Marshall"`, `max_package: "9.0"`, `is_active: "True"`, `registration_end: "23-Oct-2026"`
  2. `id: "5985"`, `company: "Valentius Kryptix"`, `max_package: "0.0"`, `is_active: "True"`, `registration_end: "21-Oct-2026"`
  3. `id: "5969"`, `company: "Eaton"`, `max_package: "10.0"`, `is_active: "True"`, `registration_end: "16-Oct-2026"`
  4. `id: "5971"`, `company: "Cummins India"`, `max_package: "25000.0"`, `is_active: "True"`, `registration_end: null`
  5. `id: "5952"`, `company: "Cummins India"`, `max_package: "25000.0"`, `is_active: "True"`, `registration_end: "14-Oct-2026"`

---

## 3. Read-Only Production SQLite Inspection

The production SQLite database at `/app/data/watcher.sqlite` was inspected directly inside container `tpo-watcher` using read-only URI connection (`file:/app/data/watcher.sqlite?mode=ro`):

### 3.1 Database Schema & Table Inventory
```text
Existing tables: ['companies', 'system_state', 'pending_notifications', 'sqlite_sequence', 'users', 'user_preferences', 'action_tokens', 'notification_deliveries']
```
- **Crucial Finding:** The table `opportunity_state` **DOES NOT EXIST** in production SQLite.

### 3.2 Companies Table Inventory & Flags
```text
Total companies: 11
- is_active = 'True':  8 rows
- is_active = 'False': 3 rows
```
Active records breakdown:
| ID | Company | First Seen At | Last Seen At | Registration End | Status in `/opportunities` |
|---|---|---|---|---|:---:|
| `5988` | Forbes Marshall | 2026-10-21 18:30 UTC | 2026-10-23 04:30 UTC | `23-Oct-2026` | Returned |
| `5985` | Valentius Kryptix | 2026-10-21 11:30 UTC | 2026-10-23 04:30 UTC | `21-Oct-2026` | Returned |
| `5969` | Eaton | 2026-10-15 11:30 UTC | 2026-10-23 04:30 UTC | `16-Oct-2026` | Returned |
| `5971` | Cummins India | 2026-10-15 11:30 UTC | 2026-10-23 04:30 UTC | `None` | Returned |
| `5952` | Cummins India | 2026-10-13 11:30 UTC | 2026-10-23 04:30 UTC | `14-Oct-2026` | Returned |
| `5869` | CITI | 2026-09-23 11:30 UTC | 2026-10-23 04:30 UTC | `24-Sep-2026` | Omitted by `LIMIT 5` |
| `5864` | Technip Energies | 2026-09-22 11:30 UTC | 2026-10-23 04:30 UTC | `23-Sep-2026` | Omitted by `LIMIT 5` |
| `5785` | Visteon | 2026-09-10 08:19 UTC | 2026-10-23 04:30 UTC | `31-Aug-2026` | Omitted by `LIMIT 5` |

### 3.3 `system_state` Keys
```text
All system_state keys:
- 'baseline_initialized': 'true'
- 'consecutive_watcher_failures': '1'
- 'last_failed_watcher_run': '2026-10-23T11:30:12.239252+00:00' (17:00 IST)
- 'last_successful_watcher_run': '2026-10-23T04:30:13.222266+00:00' (10:00 IST)
```
- **Crucial Finding:** Zero keys matching `notification:listing%` or `notification:listing-contract` exist in `system_state`.

### 3.4 Target Database Identity
Both endpoints share the exact same database:
- `routes_opportunities.py` routes query `request.app.state.opportunity_database`, which defaults to `DB_PATH = "/app/data/watcher.sqlite"`.
- Both `/api/v1/opportunities` and `/api/v1/opportunity-listing` target the identical SQLite database file.

### 3.5 Operational Telemetry & Health
- Health endpoint `/health` returned HTTP 200 OK:
  ```json
  {"status":"healthy","database":"connected","baseline_initialized":true,"watcher":"ok","deliveries":{"SENT":17}}
  ```
- No clock skew: Container clock is synchronized with EC2 host system clock within milliseconds.
- Caddy reverse proxy serves requests without stale caching headers (`Cache-Control: no-cache` on dynamic endpoints).

---

## 4. Root Cause Classification & Detailed Findings

| Condition Evaluated | Finding | Impact |
|---|---|---|
| **Missing Listing Contract** | **CONFIRMED** | `notification:listing-contract` is absent from `system_state`. |
| **Missing Per-Company Observations** | **CONFIRMED** | Zero `notification:listing:*` keys exist; `opportunity_state` table is missing. |
| **Expired Freshness Evidence** | Not Reached | Freshness cannot be evaluated because observations were never written. |
| **Active / Eligibility Filtering** | Validated | Legacy active records have `is_active='True'`, but listing projection defaults to UNVERIFIED. |
| **Frontend Validation Failure** | Rejected | `isOpportunityListing` schema validation succeeds for the `UNVERIFIED` empty envelope. |
| **HTTP Errors / 5xx** | Rejected | Both endpoints consistently return HTTP 200 OK. |
| **Database Identity Divergence** | Rejected | Both endpoints read from the same SQLite file `/app/data/watcher.sqlite`. |

### Technical Rationale: Why Legacy Polling Does Not Populate Listing Projections
Production container `tpo-watcher` executes:
```python
watcher = PlacementWatcher()
await watcher.run_forever()
```
`PlacementWatcher` (`app/monitoring/watcher.py`) is the proven production crawler. It interacts with VIERP portal endpoints, detects newly listed companies, updates `companies` and `pending_notifications`, and commits crawler heartbeat keys.

In contrast, the V2 verified listing system (`app/tpo/listing.py` and `app/monitoring/runtime_supervisor.py`) is an experimental, guarded runtime requiring isolated cryptographic state, authenticated session bindings, and explicit runtime injection (`create_application(runtime=supervisor)`). Normal production polling does NOT initialize or run this supervisor.

Therefore, normal legacy polling will **never** write `notification:listing-contract` or create per-company observation keys. Expecting `/api/v1/opportunity-listing` to populate naturally in the legacy production runtime was an invalid integration assumption.

---

## 5. Architectural Conclusion

The homepage defect was caused by:
1. `LiveOpportunities.tsx` assuming that `/api/v1/opportunity-listing` would supply active opportunities during normal production operations.
2. In the absence of a verified listing contract, `LiveOpportunities.tsx` treated the `UNVERIFIED` response as a hard empty state, concealing the 5 active portal opportunities that existed in SQLite and were visible in Section 02.
3. The solution must provide a safe, honest historical fallback in the hero component that consumes `/api/v1/opportunities` whenever verified listings are unconfirmed, without fabricating listing contracts or falsifying freshness.
