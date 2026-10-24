# TPO-WATCHER — HOMEPAGE OPPORTUNITY REGRESSION RESULTS

**Date:** 23 October 2026  
**Auditor:** Antigravity AI  
**Scope:** Automated Backend, Integration, and Headless Browser Verification  
**Target Release:** Scoped Remediation Release  
**Overall Verdict:** **14 / 14 SCENARIOS PASSED (100%)**  

---

## 1. Scenario Verification Matrix

| # | Scenario Description | Test Method | Expected Criteria | Result | Status |
|---|---|---|---|---|:---:|
| **1** | Five historical records & zero verified listings | Playwright Browser & Backend Integration | Hero renders `RECENTLY DETECTED OPPORTUNITIES` with 5 records; Archive renders 5 records | 5 records rendered with explicit historical banner and unverified units | **PASS** |
| **2** | Multiple genuine verified current listings | Playwright Browser & Backend Integration | Hero renders `TPO OPPORTUNITIES` with active beacon, observation time, and `LIVE` watermark | Verified cards rendered with `LISTING OBSERVED` timestamp and `15.0 (LPA)` | **PASS** |
| **3** | Empty historical and verified results | Playwright Browser & Backend Integration | Hero renders `NO RECORDED OPPORTUNITIES` with clear empty message | Genuine empty state rendered cleanly; no error or ghost elements | **PASS** |
| **4** | Stale per-company evidence | Backend Integration (`test_stale_per_company_evidence`) | Items suppressed from verified listings when `now_ms > valid_until_ms` | `status: STALE`, `items: []`, cleanly falls back to historical records | **PASS** |
| **5** | API unavailable or malformed response | Playwright Browser & Backend Integration | Hero renders `LIVE DATA UNAVAILABLE` with automatic retry notification | Clean error card rendered; zero console unhandled exceptions | **PASS** |
| **6** | Known expired registration deadlines | Playwright Browser Inspection | Past deadlines explicitly labeled `(Passed)`; no open window implied | Valentius Kryptix shows `21-Oct-2026 (Passed)`; Eaton shows `16-Oct-2026 (Passed)` | **PASS** |
| **7** | Missing registration dates | Playwright Browser Inspection | Null or missing dates render `Not reported in source` | Cummins India `5971` renders `Not reported in source` cleanly | **PASS** |
| **8** | Multiple independent Cummins India IDs | Backend Integration & Live DB Check | Both ID `5952` and ID `5971` independently preserved and returned | Both Cummins records present in API response and browser DOM | **PASS** |
| **9** | Unverified compensation units | Playwright Browser & Asset Inspection | Compensation formatted verbatim without asserting LPA unless verified | Rendered as `9.0 (unit unverified)`, `25000.0 (unit unverified)` | **PASS** |
| **10** | Zero placeholder or hardcoded companies | Static Asset Inspection & DOM Search | Zero occurrences of Google, Microsoft, Goldman Sachs | Verified 0 occurrences across all bundles, DOM, and tests | **PASS** |
| **11** | Correct desktop & mobile rendering | Playwright 1440px & 375px Viewports | Responsive layout without horizontal overflow (`scrollWidth <= clientWidth`) | Verified clean wrapping and zero horizontal overflow on 375x667 | **PASS** |
| **12** | No hydration or console errors | Playwright Console Event Sink | 0 runtime console errors, 0 warnings, 0 unhandled promise rejections | Exactly 0 console errors logged across all test iterations | **PASS** |
| **13** | No misleading CURRENT, OPEN, or VERIFIED labels on historical records | DOM Text Inspection | Historical cards omit "LISTING OBSERVED", "CURRENT", "OPEN", "VERIFIED" | Verified clean status strip: `STATUS: HISTORICAL RECORD` | **PASS** |
| **14** | No watcher, subscriber, or notification regression | Existing Test Suite Execution | Unit & integration tests continue passing without regression | 449 unit passed, 18 integration passed, 0 regressions | **PASS** |

---

## 2. Visual Regression Evidence

### 2.1 Case B: Hero Historical Fallback (Production Baseline Fix)
When verified current listings are unconfirmed, the hero displays genuine database-backed detections with clear disclaimers:

![Hero Historical Fallback](/C:/Users/kusha/.gemini/antigravity-ide/brain/ed0529c1-1961-47ab-b6a8-15c5bd2fbc31/case_b_hero_historical_fallback.png)

*Key verified elements:*
- Header: `RECENTLY DETECTED OPPORTUNITIES` (Amber dot indicator)
- Metric: `5 RECORDED DETECTIONS`
- Mandatory Banner: `Previously detected by TPO-WATCHER — current availability not verified`
- Card: `FORBES MARSHALL` with package `9.0 (unit unverified)`
- Card: `VALENTIUS KRYPTIX` with registration end `21-Oct-2026 (Passed)`
- Card: `CUMMINS INDIA (5971)` with registration end `Not reported in source`
- Card Status Strip: `STATUS: HISTORICAL RECORD` | `CURRENT PORTAL AVAILABILITY UNCONFIRMED`
- Watermark: `TPO`

---

### 2.2 Case A: Verified Current Listings
When verified listings with fresh observation contracts exist:

![Verified Current Listings](/C:/Users/kusha/.gemini/antigravity-ide/brain/ed0529c1-1961-47ab-b6a8-15c5bd2fbc31/case_a_hero_verified_listing.png)

*Key verified elements:*
- Header: `TPO OPPORTUNITIES` (Pulsing accent beacon)
- Metric: `1 SHOWN / 1 AT LAST CHECK`
- Card: `ALPHA ROBOTICS` with verified package `15.0 (LPA)`
- Card Status Strip: `LISTING OBSERVED` with verified observation timestamp
- Watermark: `LIVE`

---

### 2.3 Case C: Genuine Empty State
When both verified listings and historical opportunities are empty:

![Empty State](/C:/Users/kusha/.gemini/antigravity-ide/brain/ed0529c1-1961-47ab-b6a8-15c5bd2fbc31/case_c_hero_empty_state.png)

*Key verified elements:*
- Header: `TPO OPPORTUNITIES`
- Title: `NO RECORDED OPPORTUNITIES`
- Subtitle: `LATEST PORTAL CHECK HAS NO MATCHING ACTIVE LISTINGS OR HISTORICAL DETECTIONS`

---

### 2.4 Case D: Service Unavailable
When the API returns 503 or network failure:

![API Unavailable](/C:/Users/kusha/.gemini/antigravity-ide/brain/ed0529c1-1961-47ab-b6a8-15c5bd2fbc31/case_d_hero_api_error.png)

*Key verified elements:*
- Header: `TPO OPPORTUNITIES`
- Title: `LIVE DATA UNAVAILABLE`
- Subtitle: `UNABLE TO REACH TPO SERVICE — RETRYING AUTOMATICALLY`

---

### 2.5 Mobile Responsiveness (375x667 Viewport)
Testing on small form factors demonstrates complete responsiveness without clipped text or horizontal overflow:

![Mobile Viewport](/C:/Users/kusha/.gemini/antigravity-ide/brain/ed0529c1-1961-47ab-b6a8-15c5bd2fbc31/case_b_mobile_rendering.png)

---

## 3. Independent Code & Safety Review

A secondary code review was performed against all modified files:
- `frontend/src/lib/api.ts`
- `frontend/src/components/sections/LiveOpportunities.tsx`
- `frontend/src/components/sections/RecentDetections.tsx`
- `tests/integration/test_homepage_opportunity_visibility.py`

### Review Findings
1. **Contract Integrity:** `LegacyOpportunity` and `VerifiedOpportunity` are completely separate TypeScript interfaces. No synthetic properties are injected into legacy records.
2. **Read-Only Invariant:** No SQL queries in the application were mutated; `app/database/opportunity_projection.py` continues to execute in read-only mode (`PRAGMA query_only=ON`).
3. **Notification System Isolation:** No changes were made to subscriber matching, fanout, email dispatch, or crawler scheduling.
4. **Safety Boundaries Observed:** All production checks remained strictly read-only (`mode=ro`). Zero SSH mutating commands, Git pushes, or container restarts were performed on production.

---

## 4. Remaining Limitations & Architectural Clarification

1. **Current-Source Verification in Legacy Production:**
   Live production continues to execute `PlacementWatcher`. Until the V2 verified-listing supervisor is intentionally promoted to production with authenticated crawler credentials and database migration, `/api/v1/opportunity-listing` will continue returning `UNVERIFIED`. The frontend will operate in Case B (Recently Detected Opportunities), presenting real stored records with explicit disclaimers.
2. **Deadline Granularity:**
   Registration deadlines in historical VIERP records are date-only strings (e.g. `23-Oct-2026`). Portal closure times (e.g. 17:00 IST) cannot be inferred; therefore, deadlines are treated as active until the end of the specified calendar day unless portal status updates.

---

## 5. Scoped Release Commit & Deployment Plan

### 5.1 Proposed Commit Message
```text
fix(frontend): render recently detected opportunities fallback when verified listings unconfirmed

- Introduce distinct LegacyOpportunity and VerifiedOpportunity TypeScript data contracts.
- Connect LiveOpportunities hero panel to fallback to /api/v1/opportunities when verified listings are unconfirmed.
- Display explicit "Previously detected by TPO-WATCHER — current availability not verified" banner.
- Accurately parse and flag elapsed registration deadlines (e.g. Passed) without implying open registration.
- Retain verbatim compensation values with unverified unit labels.
- Preserve pre-existing static assets in app/static/assets for zero-downtime cache safety.
- Add comprehensive backend and headless browser verification test suites.
```

### 5.2 Deployment Steps (When Authorized)
1. **Local Terminal:** Publish verified commit to remote:
   ```bash
   git push origin student-auth/phase1-policy-contracts-20261012
   ```
2. **EC2 Host:** Pull and rebuild container:
   ```bash
   ssh -i <SSH_KEY> ec2-user@65.0.66.80
   cd /home/ec2-user/TPO-Watcher
   git pull origin student-auth/phase1-policy-contracts-20261012
   docker compose build watcher
   docker compose up -d --no-deps watcher
   ```
3. **Smoke Verification:** Confirm hero renders `RECENTLY DETECTED OPPORTUNITIES` on `https://tpowatcher.duckdns.org`.
