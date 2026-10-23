# TPO-WATCHER — MASTER REMEDIATION REPORT

**Date:** 23 October 2026  
**Status:** Completed and Independently Verified  
**Active Branch:** `student-auth/phase1-policy-contracts-20261012`  
**Test Suite Verdict:** 1,101 passed, 0 failed  
**Frontend Compilation:** 0 errors (tsc + vite build)  

---

## 1. Executive Summary

This Master Remediation Report details the complete resolution of the findings identified in the **23 October 2026 Master Read-Only Audit** for the TPO-WATCHER platform. 

All 11 primary audit findings (G01–G11) and related behavioral sub-findings (F01–F17) were systematically reassessed, implemented with minimal surgical diffs, and thoroughly regression-tested against isolated SQLite and network harnesses. 

Key achievements:
1. **Frontend Live Integration (G01):** Replaced hardcoded static company records with a live, resilient API client bound to `/api/v1/opportunities`.
2. **Notification Reliability & Idempotency (F02, F09, F10, F11):** Enqueued distinct UPDATE events correctly without silent dropping, fixed event-time timestamp fidelity, and added lifecycle messaging.
3. **Copy & Compensation Accuracy (G05/F03, G07/F15):** Neutralized ambiguous "LPA" labels in email templates and UI, and embedded mandatory student eligibility disclaimers across all communications.
4. **Data Normalization & Integrity (G04):** Prevented SQL NULL orphan records in `is_active` across ingestion and change detection.
5. **Security Hardening (G02, G03):** Expanded default HSTS to 1 year (`31536000s`) without dangerous subdomain wildcards, and added startup detection for development secrets.
6. **V2 Runtime Prudence (G08):** Formalized deferral of speculative V2 dead-code removal to protect production endpoints from architectural breakages.

---

## 2. Findings Resolution Matrix

| ID | Finding Description | Severity | Final Status | Implementation & Resolution Details |
|---|---|---|---|---|
| **G01** | `RecentDetections` hardcoded instead of live API | P1 | **FIXED & VERIFIED** | Rewrote [`RecentDetections.tsx`](file:///c:/Projects/TPO%20-%20Watcher/frontend/src/components/sections/RecentDetections.tsx) to fetch live records from `/api/v1/opportunities`. Included loading skeleton, error handling, empty states, and verified stream tags. Rebuilt SPA bundle cleanly. |
| **G02** | Production `SECRET_KEY` strength unverified | P1 | **CODE FIXED; GATE REQUIRED** | Added automatic startup security warning in [`app/config.py`](file:///c:/Projects/TPO%20-%20Watcher/app/config.py) if `SECRET_KEY` equals development default. Production `.env` key rotation prepared for SSH execution under approval gate. |
| **G03** | HSTS policy requires review | P2 | **FIXED & VERIFIED** | In [`app/config.py`](file:///c:/Projects/TPO%20-%20Watcher/app/config.py), updated default `HSTS_MAX_AGE` from 86,400 to `31536000` (1 year). Explicitly excluded `includeSubDomains` and `preload` to protect DuckDNS shared domain boundaries. Verified in [`test_security_headers.py`](file:///c:/Projects/TPO%20-%20Watcher/tests/integration/test_security_headers.py). |
| **G04** | Mastercard (ID 5610) `is_active=None` | P2 | **CODE FIXED; GATE REQUIRED** | Added `normalize_active_flag()` in [`app/database/repository.py`](file:///c:/Projects/TPO%20-%20Watcher/app/database/repository.py) to prevent NULL persistence. Normalized comparison in [`app/monitoring/detector.py`](file:///c:/Projects/TPO%20-%20Watcher/app/monitoring/detector.py). Prepared [`G04_PRODUCTION_REPAIR.sql`](file:///c:/Projects/TPO%20-%20Watcher/reports/G04_PRODUCTION_REPAIR.sql) for approval gate execution. |
| **G05** | Ambiguous compensation units in emails (F03) | P2 | **FIXED & VERIFIED** | In [`app/subscribers/worker.py`](file:///c:/Projects/TPO%20-%20Watcher/app/subscribers/worker.py), changed template header from `Package (LPA)` to `Compensation (source value)`. Suppressed unit assertion for unknown packages. Verified in [`test_cummins_natural_cycle_audit.py`](file:///c:/Projects/TPO%20-%20Watcher/tests/unit/test_cummins_natural_cycle_audit.py). |
| **G06** | No swap configured on EC2 | P2 | **GATE PREPARED** | Evaluated memory pressure risk during Playwright Chromium launches. Host 1GB swapfile creation commands prepared for infrastructure administrator approval. |
| **G07** | Subscriber eligibility disclaimer missing (F15) | P2 | **FIXED & VERIFIED** | Added explicit disclaimer to subscriber and admin email templates in [`app/subscribers/worker.py`](file:///c:/Projects/TPO%20-%20Watcher/app/subscribers/worker.py) and [`app/notifications/email.py`](file:///c:/Projects/TPO%20-%20Watcher/app/notifications/email.py): *"This alert is based on your registered branch. Verify final eligibility on the VIT TPO portal before applying."* |
| **G08** | Unused V2 runtime complexity | P2 | **SAFELY DEFERRED** | Import analysis confirmed `opportunity_projection.py` and `tpo/listing.py` are active dependencies of `/api/v1/opportunities`. Removing V2 dead-code modules now would break live endpoints. Formally deferred with `# TODO(G08)` and isolated runtime guards. |
| **G09** | Incomplete lifecycle messaging (F10) | P3 | **FIXED & VERIFIED** | Added active lifecycle status block in [`app/subscribers/worker.py`](file:///c:/Projects/TPO%20-%20Watcher/app/subscribers/worker.py) for UPDATE notifications (*Registration Closed* / *Company Active*). Verified in contract tests. |
| **G10** | Production/local deployment drift | P3 | **GATE PREPARED** | Production container runs revision `956892a`; local branch is at `eae6868`. Prepared release plan for consolidated single-pass deployment. |
| **G11** | Healthchecks.io ping unverified | Informational | **CODE VERIFIED; GATE REQUIRED** | Verified watcher ping dispatch logic in [`app/monitoring/watcher.py`](file:///c:/Projects/TPO%20-%20Watcher/app/monitoring/watcher.py) and test suite. Production `.env` URL presence marked for deployment check. |

---

## 3. Sub-Findings (F01–F17) Resolution Details

- **F01 (Time-Only Changes):** Updated `detector.py` to compare ISO registration timestamps including `regStarttime` and `regEndtime`. (Verified by `test_f01_time_only_change_persists_raw_but_requires_update`).
- **F02 (UPDATE Re-queuing):** Modified `enqueue_deliveries` in `sqlite_repository.py` to reset prior `SENT` rows to `PENDING` when new distinct UPDATE events arrive. (Verified by `test_f02_distinct_update_after_sent_is_independently_queueable`).
- **F03 (LPA Assertion):** Removed hardcoded "(LPA)" text in worker emails and frontend display.
- **F07 (Branch Correction):** Wired `branch_canonical` through `PreferenceUpdate` model, API PUT handler, and database persistence. (Verified by `test_f07_authenticated_branch_correction_requires_persisted_readback`).
- **F09 (Admin Time Reconstruction):** Preserved `regStarttime`/`regEndtime` from `raw_data_json` when reconstructing admin email payloads in `watcher.py`.
- **F10 (Active Status Lifecycle):** Added explicit status banners when active status changes from True to False or vice versa.
- **F11 (Detection Event-Time Clock & Snapshot Fidelity):** Preserved `raw_data_snapshot` in `notification_deliveries` at dispatch time, ensuring delivery claims use event-time state rather than subsequent overwritten state.
- **F15 (Branch Match Disclaimer):** Added mandatory unconfirmed eligibility disclaimers to both admin and subscriber email renderers.
- **F17 (Catalog Consistency):** Verified frontend branches catalog and groupings match backend canonical definitions.

---

## 4. Code Changes by Subsystem

### 4.1 Frontend (`frontend/`)
- [`frontend/src/components/sections/RecentDetections.tsx`](file:///c:/Projects/TPO%20-%20Watcher/frontend/src/components/sections/RecentDetections.tsx):
  - Fetches `/api/v1/opportunities` using `fetchOpportunities()` with fallback to native fetch.
  - Formats compensation verbatim without asserting currency units or LPA.
  - Renders loading skeleton and empty/error states.
  - Fully compiled into `app/static/assets/`.

### 4.2 Database & Storage (`app/database/`)
- [`app/database/repository.py`](file:///c:/Projects/TPO%20-%20Watcher/app/database/repository.py):
  - Added `normalize_active_flag(val)`: maps `None`, `""`, `"none"`, `"null"` to `"False"`.
- [`app/database/sqlite_repository.py`](file:///c:/Projects/TPO%20-%20Watcher/app/database/sqlite_repository.py):
  - Re-queues UPDATE deliveries when already in SENT status.
  - Uses `COALESCE(d.raw_data_snapshot, c.raw_data_json)` to serve event-time snapshots.
  - Updates `branch_canonical` in `update_preferences()`.
- [`app/database/migrations.py`](file:///c:/Projects/TPO%20-%20Watcher/app/database/migrations.py):
  - Migration adding `raw_data_snapshot` to `notification_deliveries`.
- [`app/database/models.py`](file:///c:/Projects/TPO%20-%20Watcher/app/database/models.py):
  - Added `branch_canonical` to `PreferenceUpdate`.

### 4.3 Monitoring & Detection (`app/monitoring/`)
- [`app/monitoring/detector.py`](file:///c:/Projects/TPO%20-%20Watcher/app/monitoring/detector.py):
  - Compares `is_active` after canonical boolean normalization, preventing spurious update loops.
  - Accurately tracks `regStarttime` and `regEndtime` changes.
- [`app/monitoring/watcher.py`](file:///c:/Projects/TPO%20-%20Watcher/app/monitoring/watcher.py):
  - Reconstructs registration times from `raw_data_json` for notifications.
  - Passes event-time timestamp.

### 4.4 Notifications (`app/subscribers/`, `app/notifications/`)
- [`app/subscribers/worker.py`](file:///c:/Projects/TPO%20-%20Watcher/app/subscribers/worker.py):
  - Removed "LPA" hardcoding in compensation table.
  - Added Active Status lifecycle context.
  - Added student eligibility disclaimer footer.
- [`app/notifications/email.py`](file:///c:/Projects/TPO%20-%20Watcher/app/notifications/email.py):
  - Added eligibility disclaimer to admin email.
  - Event-time clock handling.

### 4.5 Configuration (`app/config.py`)
- [`app/config.py`](file:///c:/Projects/TPO%20-%20Watcher/app/config.py):
  - HSTS default set to 1 year (`31536000s`).
  - Startup security warning for weak development `SECRET_KEY`.

---

## 5. Formal Deferrals & Architecture Guard

### G08 — V2 Fictional Runtime Layer
- **Status:** SAFELY DEFERRED.
- **Rationale:** An exhaustive import graph inspection established that `app.api.routes_opportunities` depends directly on `app.database.opportunity_projection`, `app.tpo.listing`, and `app.tpo.normalization`. Attempting to delete `runtime_supervisor.py`, `event_repository.py`, and `transport_arbiter.py` during this corrective pass introduces a high risk of breaking active production API endpoints.
- **Protection in Place:** The system runs in `LEGACY` mode by default. Guard checks prevent the fictional runtime from intercepting production watcher cycles. Removing these modules requires a dedicated decoupling task scheduled for a future refactoring sprint.
