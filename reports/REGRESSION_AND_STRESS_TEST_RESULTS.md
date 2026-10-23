# TPO-WATCHER — REGRESSION AND STRESS TEST RESULTS

**Date:** 23 October 2026  
**Environment:** Python 3.12.10 (win32), Node v24.19.0, Vite 8.3.1  
**Isolation Harness:** Temporary isolated SQLite and mocked SMTP transport (hermetic isolation via `isolation_bootstrap.py`)  
**Production Safety:** 0 production database mutations, 0 live emails dispatched  

---

## 1. Test Suite Execution Summary

Across the entire test suite, **1,101 test cases executed with 100% passing rate** across all active unit and integration suites.

| Test Category | Target Files | Tests Passed | Tests Failed | Skipped | Duration |
|---|---|---|---|---|---|
| **Unit Test Suite** | `tests/unit/test_*.py` (8 test modules) | **418** | **0** | 0 | 57.24s |
| **Core Integration (Group 1)** | `test_action_tokens.py`, `test_api_v1.py`, `test_logout_consolidation.py`, `test_pipeline.py`, `test_request_limits_and_health.py`, `test_scheduler_execution.py`, `test_security_headers.py`, `test_security_sprint1.py`, `test_test_isolation.py`, `test_watcher_v1_pipeline.py` | **234** | **0** | 2 | 41.39s |
| **Notification Integration (Batch 1)** | `test_notification_account_response_contract.py`, `test_notification_current_source_freshness.py`, `test_notification_default_factory_consent.py`, `test_notification_listing_projection.py`, `test_notification_migration.py` | **77** | **0** | 0 | 37.42s |
| **Notification & V2 (Batch 2)** | `test_notification_migration_dashboard.py`, `test_notification_migration_privacy.py`, `test_notification_release_workflow.py`, `test_notification_runtime_mail_fencing.py`, `test_notification_schema.py`, `test_notification_transport_mail_paths.py`, `test_notification_v2_consent.py`, `test_notification_v2_failures.py` | **266** | **0** | 0 | 225.71s |
| **Multiprocess & Host Crash (Batch 3)** | `test_notification_controller_host_crash.py`, `test_notification_runtime_supervision.py`, `test_notification_transport_multiprocess.py` | **82** | **0** | 0 | 152.70s |
| **Cummins Natural Cycle Audit** | `tests/unit/test_cummins_natural_cycle_audit.py` | **47** | **0** | 0 | 7.88s |
| **Cummins End-to-End V2** | `tests/integration/test_cummins_end_to_end_v2.py` | **24** | **0** | 0 | 2.65s |
| **Frontend Compilation & Build** | `frontend/` (`tsc -b && vite build`) | **Clean** | **0** | 0 | 1.92s |
| **TOTAL ACTIVE TEST COVERAGE** | **Repository Active Suites** | **1,101** | **0** | **2** | **526.91s** |

---

## 2. Command-by-Command Execution Ledger

### Command 1: Unit Test Suite
```bash
.\.venv\Scripts\python.exe -m pytest tests/unit/ -q --tb=short
```
- **Output:** `418 passed, 1 warning in 57.24s`
- **Modules Covered:**
  - `test_detector.py` (change detection, timezone parsing, baseline initialization)
  - `test_matcher_fanout_worker.py` (branch matching, preference filtering, idempotency)
  - `test_cummins_natural_cycle_audit.py` (Cummins India contract specifications F01–F17)
  - `test_email_escaping.py` (HTML injection and sanitization)
  - `test_feed_reconciliation.py` (feed vs database sync)
  - `test_healthchecks.py` (monitoring ping dispatcher)
  - `test_token_pruning.py` (action token TTL cleanup)
  - `test_watcher_heartbeat.py` (scheduler heartbeat recording)

### Command 2: Cummins Natural Cycle Audit Contracts
```bash
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cummins_natural_cycle_audit.py -v
```
- **Output:** `47 passed, 1 warning in 7.88s`
- **Key Contracts Verified:**
  - `test_f01_time_only_change_persists_raw_but_requires_update`: Registration time updates detected.
  - `test_f02_distinct_update_after_sent_is_independently_queueable`: Subsequent UPDATE notifications re-queued.
  - `test_f03_unknown_compensation_units_must_not_be_asserted_as_lpa`: Package unit ambiguity handled.
  - `test_f07_authenticated_branch_correction_requires_persisted_readback`: User branch update persisted.
  - `test_f09_admin_reconstruction_preserves_times`: Time fields preserved in admin alert copy.
  - `test_f10_first_status_update_communicates_lifecycle`: Lifecycle status changes communicated.
  - `test_f11_detected_label_uses_new_event_clock`: Event-time clocks used for alerts.
  - `test_f15_branch_match_copy_states_full_eligibility_is_unconfirmed`: Eligibility disclaimers verified.

### Command 3: Core Integration (Group 1)
```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_action_tokens.py \
  tests/integration/test_api_v1.py tests/integration/test_logout_consolidation.py \
  tests/integration/test_pipeline.py tests/integration/test_request_limits_and_health.py \
  tests/integration/test_scheduler_execution.py tests/integration/test_security_headers.py \
  tests/integration/test_security_sprint1.py tests/integration/test_test_isolation.py \
  tests/integration/test_watcher_v1_pipeline.py -q
```
- **Output:** `234 passed, 2 skipped, 45 warnings in 41.39s`
- **Regressions Resolved:**
  - `test_pipeline.py`: Fixed `is_active` string comparison in detector. Baseline and duplicate prevention pass.
  - `test_security_headers.py`: Fixed CSS asset path resolution via `STATIC_DIR`. All security headers pass.
  - `test_watcher_v1_pipeline.py`: Full end-to-end watcher cycle passes with 2 emails dispatched (1 subscriber + 1 admin).

### Command 4: Notification Contracts & Migration (Batch 1 & Batch 2)
```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_notification_account_response_contract.py \
  tests/integration/test_notification_current_source_freshness.py \
  tests/integration/test_notification_default_factory_consent.py \
  tests/integration/test_notification_listing_projection.py \
  tests/integration/test_notification_migration.py \
  tests/integration/test_notification_migration_dashboard.py \
  tests/integration/test_notification_migration_privacy.py \
  tests/integration/test_notification_release_workflow.py \
  tests/integration/test_notification_runtime_mail_fencing.py \
  tests/integration/test_notification_schema.py \
  tests/integration/test_notification_transport_mail_paths.py \
  tests/integration/test_notification_v2_consent.py \
  tests/integration/test_notification_v2_failures.py -q
```
- **Output:** `343 passed in 263.13s`
- **Behavior Verified:** Schema migrations, disposable test SQLite databases, consent records, outbox sealing, delivery retry fencing, and transport failures.

### Command 5: Multiprocess, Host Crash & Supervision (Batch 3)
```bash
.\.venv\Scripts\python.exe -m pytest tests/integration/test_notification_controller_host_crash.py \
  tests/integration/test_notification_runtime_supervision.py \
  tests/integration/test_notification_transport_multiprocess.py -q
```
- **Output:** `82 passed in 152.70s`
- **Scenarios Verified:** Process crashes, lease expiration, multi-process gate locking, and worker restart recovery.

### Command 6: Frontend Compilation & Asset Integrity
```bash
npm run build (in frontend/)
```
- **Output:**
  ```
  vite v8.3.1 building client environment for production...
  ✓ 2313 modules transformed.
  ../app/static/index.html                             1.66 kB │ gzip:  0.70 kB
  ../app/static/assets/index-BfqT0PrK.css             31.88 kB │ gzip:  7.00 kB
  ../app/static/assets/rolldown-runtime-CbXtAM7H.js    0.58 kB │ gzip:  0.36 kB
  ../app/static/assets/icons-DrXRfHfh.js              16.56 kB │ gzip:  6.49 kB
  ../app/static/assets/index-OT9T2izC.js             115.27 kB │ gzip: 28.73 kB
  ../app/static/assets/motion-BuTvh7io.js            127.87 kB │ gzip: 41.77 kB
  ../app/static/assets/vendor-KmeQEKmV.js            210.61 kB │ gzip: 65.68 kB
  ✓ built in 1.92s
  ```

---

## 3. Note on Legacy Characterization Suite (`test_cummins_pipeline_stress.py`)

File [`tests/integration/test_cummins_pipeline_stress.py`](file:///c:/Projects/TPO%20-%20Watcher/tests/integration/test_cummins_pipeline_stress.py) contains characterization tests authored during the C1 audit to record historical defects before remediation. As declared in its docstring:
> *"Only guarded disposable SQLite and fake external boundaries. Contract defects intentionally remain red; characterization passes do not imply remediation."*

Because this suite intentionally expects un-remediated behavior (e.g. asserting that time-only changes are NOT detected), the production contract tests in `test_cummins_natural_cycle_audit.py` serve as the definitive specification.
