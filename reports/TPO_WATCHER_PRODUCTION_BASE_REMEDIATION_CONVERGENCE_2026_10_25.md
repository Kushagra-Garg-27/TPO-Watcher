# Production-base remediation convergence — 25 October 2026

**LOCAL_REMEDIATION_VERIFIED / PRODUCTION_RELEASE_HOLD.** Reviewed candidate
`e761f8d` preserves the deployed `eb086ae` diagnostics and existing remediation,
closes the reproduced shared-logging gaps, and passes the complete Windows suite.
Source recovery and exact-tip native qualification remain unresolved; notification
reliability is not yet verified in production. No production operation was performed.
This supplements the existing remediation record without restarting the master audit.

## Exact revisions and preservation

| Role | SHA |
| --- | --- |
| Latest production base supplied by the user | `eb086aee0e26158f7194758dc72ea11c21db609f` |
| Preserved original notification candidate | `4208e31c2fa8c25f88ef0244e0a4d262f3f5970d` |
| Preserved IDE 2 remediation tip | `7a2217dd3b46781e638b4b64fcc9450f28b518e7` |
| Existing integration, parents exactly production + remediation | `053b152b37d8aea8d5b4c74606765cdc73565136` |
| Shared-logging privacy follow-up | `ccfbca7a62f781976dc4711ea47dfd332986478a` |
| Bounded redaction and corrected async-outage fixture | `4bc940d5e15e2571fed5d8bcdb0b9a216ce75e56` |
| Final integrated candidate: credential-overlap review correction | `e761f8d47d8f3827c685bcecd7aab6adf5afe378` |

The original worktrees and branches were clean and were preserved. The follow-up
uses a new managed worktree, `codex/audit-privacy-convergence-20261025`, based on
`053b152`; no existing branch was reset, rebased or rewritten. The recovery-tooling
integration lineage is excluded. The current candidate descends from `eb086ae`.

Git-blob comparisons against `eb086ae` establish unchanged bytes for:
`app/tpo/client.py`, `tests/unit/test_tpo_response_diagnostics.py`,
`app/tpo/listing.py`, `app/tpo/models.py`, `Dockerfile`, `docker-compose.yml`,
`tests/isolation_bootstrap.py` and `tests/conftest.py`.
The client blob SHA-256 remains
`7f017cc5e660b09f46725766f9026beac354ea8a4e863cdce6733822661499f6`.

## Disposition of the assigned findings

These are targeted continuations of already documented findings, not new audit
claims. Existing fixes were retained and exercised by their real SQLite/fake
transport regressions.

| Finding | Local disposition and remaining limits |
| --- | --- |
| F02: partial-feed absence | Default-off absence reconciliation; explicit authoritative completeness is required. No production application path supplies that authority. Positive observations still update/reactivate records. Stale stored active flags can persist; their meaning must be accepted explicitly at release. |
| F05: company/event durability | `apply_company_event` commits the company transition and durable event obligation/snapshot in one `BEGIN IMMEDIATE` unit. Crash and concurrent-introduction tests retain consistent units and converge on replay. This is per-company atomicity, not whole-feed atomicity. |
| F06: terminal UPDATE | A genuinely distinct later UPDATE can requeue a terminal delivery after preserving the prior outcome. Same-event replay remains terminal; lifetime NEW deduplication remains. The outcome log is append-only with no approved pruning policy. |
| F07: deferred snapshot | Durable obligations retain event-time snapshots; deferred B/C events retain their own content even when the latest company row has advanced. Equivalent deadline representations do not create new events. Legacy NULL snapshots fall back to current records without fabricating history. Current consent/preferences remain checked before transport. |
| F08: privacy | Existing provider, rate-limit and authentication fixes retained. This continuation reproduced three remaining watcher exception-string leaks and missing protection at the shared console/Uvicorn error sink; the four-file follow-up below addresses those boundaries. |
| F09: observation identities | Whole-list identity validation rejects conflicting duplicate IDs, unstable identities and blank names before business writes; identical duplicate records deduplicate. Source schema/status acceptance is unchanged. |

Earlier supporting commits remain identifiable: atomic/event work
`25c6c004742b52c198c20e4ab682849751b17215`, privacy call-site work
`7c24c7f22d3e4b84c0ff995f493e4821be6e0d51`, completeness/identity tests
`408c35a9f03a5d74e773ccdf9319da1e4a32c3f1`, and final event/drain/readiness
safety changes `7a2217d`. Other existing local changes cover queue/readiness,
state-neutral signup, truthful frontend copy/deadline handling and backup tooling;
their policy and release implications remain part of review, not implicit approval.

## This task's five-file correction

- `app/logging_config.py`: protects the shared console formatter against configured
  credentials, email addresses, HTTP/WS URLs, common credential fields and private
  headers. Exception arguments, exception-object messages, traceback text, cached
  exception text and stack text are withheld; malformed formatting yields a fixed
  category instead of the standard logging error dump. Messages over 4,096 characters are
  withheld entirely before pattern redaction to bound its work and avoid leaking
  partial credentials through truncation. Quoted header keys are covered too.
  Configured credentials are matched against the original message and overlapping
  spans merged, preventing a username replacement from exposing password fragments.
  Formatting uses a copy of the record. Uvicorn loggers propagate to the same sink.
- `app/main.py`: `log_config=None` prevents Uvicorn from replacing that protected
  sink with its default formatter. No request serving or access-log setting changes.
- `app/monitoring/watcher.py`: authentication, pruning-loop and scheduler-loop
  errors use fixed messages without exception strings. Retry, scheduling,
  heartbeats, validation, reconciliation and dispatch control flow are unchanged
  by this follow-up.
- `tests/integration/test_shared_logging_privacy.py`: 21 fictional canary cases
  exercise the actual console boundary, Uvicorn configuration, watcher error
  records, unchanged structural evidence, failure heartbeat and no source/dispatch
  activity after the synthetic pre-fetch authentication failure.
- `tests/integration/test_queue_availability_and_readiness.py`: fixes an existing
  async-client fixture exposed by the full-suite warning review. Previously the
  constructor itself was a coroutine, so the test failed on missing
  `fetch_companies` rather than the intended source outage. The corrected fake
  preserves the real client boundary and asserts exactly one awaited fetch,
  one failure-counter increment, no dispatch before draining, and successful
  subsequent local queue progress. No application drain policy changed.

This is defense in depth. Pattern redaction cannot identify arbitrary sensitive
prose or arbitrary raw bodies; call sites must continue to use fixed categories.
It does not sanitize independent files, direct `print`, historical logs, or future
third-party handlers that bypass the configured sink. Full exception/stack details
are deliberately unavailable in the shared sink. No raw production values were
used in the tests.

## Verification receipts

- Test-first reproduction: 12 privacy regressions failed against unchanged
  `053b152`, while the fixed structural-evidence case passed. An additional
  exception-object-message test independently failed before its fix.
- Three added header/oversize cases and the strengthened outage-boundary assertion
  failed before their corrections: **4 failed**, exit 1. No warning filter was used.
- Initial shared-sink suite: **15 passed**, exit 0, 2.16 s; final file has 21 cases.
- Final focused source/privacy/event/identity/completeness/queue/heartbeat/scheduler
  suite: **180 passed**, exit 0, 1 Starlette deprecation warning, 26.94 s.
- Frontend deadline contracts: `node --test frontend/tests/deadline.test.mjs`,
  **4 passed**, 0 failed/skipped, exit 0.
- First complete suite at `ccfbca7`: **1,330 passed, 0 failed, 2 skipped,
  46 warnings**, exit 0, 695.19 s. Run UTC 07:58:57–08:10:34
  (13:28:57–13:40:34 IST). Its unawaited-coroutine warning led to the explicit
  fixture correction above; this result is preserved as an intermediate receipt.
- Intermediate full run at `4bc940d`: deliberately **ABORTED**, process exit 1,
  UTC 08:14:19–08:20:01 (13:44:19–13:50:01 IST), after review established the
  credential-overlap defect below. Only this uniquely identified local test
  process tree was stopped. There is no full-suite pass/fail verdict for that run.
- Final complete suite at exact `e761f8d`: **1,336 passed, 0 failed, 2 skipped,
  45 warnings**, exit 0, **667.59 s**. Run UTC **08:23:39–08:34:48**
  (**13:53:39–14:04:48 IST**), 25 October 2026. All 1,338 collected cases accounted
  for. Warnings are dependency `DeprecationWarning`/`StarletteDeprecationWarning`;
  the unawaited-coroutine warning is absent. The only skips are the existing
  Windows-unavailable native `sendmsg` and Linux owned-directory-descriptor cases.
- Final full receipt:
  `C:/Users/kusha/.codex/private/tpo-watcher/audit-privacy-convergence-e761f8d-full.log`,
  SHA-256 `ab5706bb30a26988b8f22683f647d958f6fc2b0fb25c2fea420a4f0630cc9bf7`.
  The application/test tree remained unchanged throughout the exact-tip run.
- Interpreter: Windows CPython **3.12.10**. All 13 direct installed dependency
  versions match the candidate's `requirements.txt` pins.
- Entry point: `python -I -B tests/isolation_bootstrap.py --pytest -q --tb=short -rs`.
  Existing process-wide guards install before app imports, confine SQLite to
  disposable paths, deny external network/SMTP/browser/CLI activity and check
  suppressed violations. The established local-only HTTP and controlled synthetic
  child-process tests remain permitted by the existing guard.
- No tests or isolation safeguards were removed, weakened or skipped by this
  follow-up. The approved deadline-update characterization is retained.
- AST comparison with logging calls and unused exception bindings removed proves
  the entire watcher control flow is unchanged between `053b152` and `ccfbca7`.

Prior evidence is kept separate: the original **1,310/0/2** result belongs to the
notification-plus-recovery-tooling lineage. Existing `053b152` has a recorded
**1,315/0/2** Windows receipt and later native ARM64/container qualification.
Those platform qualifications do **not** automatically qualify `e761f8d`.

Independent read-only review of this five-file follow-up identified one P2:
sequential replacement of a username could expose fragments of a containing
password. Three added real-sink regressions failed before the fix (contained,
partially overlapping and repeated credentials). The final span-based correction
passes all three; the reviewer confirmed the P2 resolved and found no other
concrete regression in the scoped delta, with merge readiness contingent on the
exact-tip complete suite. This is not a new master audit or a review of unknown
upstream response semantics.

The seven historical Scope A stress failures remain present in
`tests/integration/test_cummins_pipeline_stress.py` and execute in the complete
suite. Their previous failures are not erased by the newer candidate's results:

```text
test_f05_deterministic_two_connections_require_disjoint_claims
test_f06_failed_fanout_after_persistence_is_replayed
test_f12_exhausted_crash_recovery_requires_terminal_state
test_f13_stale_release_cannot_overwrite_newer_sent
test_f16_admin_exception_must_not_block_student_processing
test_f10_queued_new_after_feed_deactivation_requires_freshness
test_full_no_reset_natural_cycle_characterization
```

Their fixes and the explicitly approved deadline characterization belong to the
preserved earlier notification/remediation commits. This follow-up adds its
privacy test file and changes none of those stress contracts.

## Source state and release gates

No new production check was performed. The user-supplied latest verified base is
`eb086ae`, image
`sha256:1a3df262bb0d22d3e2bc7b82306ba942198d34e4d3c5f09900c406a93189de84`.

An existing local evidence report in `C:/Projects/TPO-Watcher-Integration/reports/`
(`TPO_WATCHER_NATURAL_SOURCE_DIAGNOSTIC_AND_RELEASE_DECISION.md`, read-only
observation recorded at 13:07 IST) reports the 10:00 IST natural cycle failed with
`TimeoutError`, failed heartbeat `2026-10-25T04:30:53.411269+00:00`, failure count 6,
and no response-shape classification. It reports 5 subscribers, 11 companies
(8 stored active), 17 SENT deliveries and no new pending/dispatch activity.
These are previously recorded observations, not a fresh live verification here.
The last classified older failure remains `invalid_status`; its actual string
semantics are still unknown. A timeout and missing classification do not establish
authentication success, that an HTTP response was received, or an upstream root
cause. No parser correction is justified by this evidence.

Remaining gates:

1. Exact-tip Windows regression and the scoped privacy review are complete.
   Native Linux/ARM64 and runtime-image qualification for the new SHA remain to be
   established locally or through separately authorized CI; older qualifications
   cannot be presented as new-SHA results. No CI push/dispatch was performed.
2. Source recovery remains a separate evidence-gated workstream. No source-fix
   commit exists in this continuation. Do not claim production notification or
   ingestion reliability from local tests. The next source observation must be a
   separately authorized read of existing natural-cycle evidence; no manual poll.
3. Any notification/data-safety release while source recovery is unresolved needs
   a separate risk-reviewed human decision. The integrated scope includes additive
   SQLite/event changes and completeness behavior; it is not a diagnostic-only
   release. Preserve pending-delivery ownership, use the documented no-overlap
   worker protocol and explicitly review default-OFF independent draining, retained
   stale active flags and outcome-log retention. SMTP delivery remains at least
   once: a provider acceptance followed by a lost database acknowledgement can
   still cause a retry/duplicate; ambiguity tracking does not promise exactly once.
4. Reconfirm the actual production SHA before eventual integration/deployment. If
   production advances, preserve its exact source diagnostic/recovery bytes and
   rerun affected and full tests on the new integration SHA.
5. Fresh protected transactional backup, upload/encryption/list receipt and the
   immediately preceding rollback image are future deployment gates. The formal
   historical `SCOPE_A_CONFIGURATION_UNVERIFIED` limitation remains. No real S3
   recovery drill is established; restore access and the temporary SSH-rule
   lifecycle remain separately authorized operations.

Source recovery, event durability and this privacy follow-up remain distinguishable
by commit. Lower-priority operational/frontend changes already present in
`053b152` have not been silently removed. A reduced cherry-pick release would be a
different, untested candidate and needs its own dependency review and test receipt.

No push, CI dispatch, production access, deployment, restart, source request,
notification/email, live SQLite write, restore, AWS change or SSH-rule removal
was performed. Existing production authorizations were not reused.

## Complete integrated changed-file inventory

`git diff --name-status eb086ae e761f8d` contains **62 paths**: 35 modified,
19 added, 8 removed. Removed paths are superseded generated frontend assets.
The report being written here is a later documentation-only artifact, outside
the tested application commit.

```text
M .github/workflows/ci.yml
M README.md
M app/api/rate_limiter.py
M app/api/routes_auth.py
M app/auth/manager.py
M app/config.py
M app/database/interfaces.py
M app/database/migrations.py
M app/database/repository.py
M app/database/sqlite_repository.py
M app/health/server.py
M app/logging_config.py
M app/main.py
M app/monitoring/detector.py
M app/monitoring/watcher.py
M app/notifications/email.py
D app/static/assets/icons-DrXRfHfh.js
D app/static/assets/index-BdLrdtJB.css
D app/static/assets/index-BfqT0PrK.css
D app/static/assets/index-C3ja19IR.js
D app/static/assets/index-DQ_HPArr.js
A app/static/assets/index-DgZV0yRc.js
D app/static/assets/index-OT9T2izC.js
D app/static/assets/motion-BuTvh7io.js
D app/static/assets/vendor-KmeQEKmV.js
M app/static/index.html
M app/subscribers/worker.py
M docs/ci_and_supply_chain.md
A docs/deployment_baseline_capture.md
A docs/deployment_worker_compatibility.md
A docs/prepared_source_classification_design.md
A docs/recovery_drill_prerequisites.md
A docs/ssh_access_lifecycle.md
M frontend/src/components/sections/HeroSection.tsx
M frontend/src/components/sections/LiveOpportunities.tsx
M frontend/src/components/sections/MonitoringPipeline.tsx
M frontend/src/components/sections/RecentDetections.tsx
A frontend/src/lib/deadline.ts
M frontend/src/pages/SignupPage.tsx
A frontend/tests/deadline.test.mjs
M infra/systemd/README.md
A reports/TPO_WATCHER_MASTER_AUDIT_REMEDIATION_FINAL.md
M requirements.txt
M scripts/backup_sqlite.py
M scripts/restore_test.py
M tests/integration/test_api_v1.py
A tests/integration/test_cross_release_worker_protocol.py
M tests/integration/test_cummins_pipeline_stress.py
A tests/integration/test_event_safety_integration.py
A tests/integration/test_notification_event_durability.py
A tests/integration/test_notification_v1_reliability.py
A tests/integration/test_queue_availability_and_readiness.py
A tests/integration/test_shared_logging_privacy.py
A tests/integration/test_signup_response_parity.py
M tests/unit/test_backup_tooling.py
M tests/unit/test_cummins_natural_cycle_audit.py
M tests/unit/test_detector.py
A tests/unit/test_feed_completeness_gate.py
M tests/unit/test_feed_reconciliation.py
M tests/unit/test_healthchecks.py
A tests/unit/test_log_privacy_canaries.py
A tests/unit/test_observation_identity.py
```
