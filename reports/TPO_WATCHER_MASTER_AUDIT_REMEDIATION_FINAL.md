# TPO-WATCHER — Master Audit Remediation and Final Engineering Closure

**Final state: `LOCAL_REMEDIATION_COMPLETE_RELEASES_PENDING`**

Local engineering remediation of the 24/25 October 2026 master-audit findings is
complete and regression-verified on this branch. Nothing is deployed: every
production action (source-status decision, release, rollback, drill, AWS/SSH
change) remains gated on separate explicit authorization and, for source
recovery, on evidence that does not yet exist.

## 1. Executive verdict

| Item | Result |
| --- | --- |
| Final state category | `LOCAL_REMEDIATION_COMPLETE_RELEASES_PENDING` |
| Final commit / branch | `0dfd1a9f155ac34840ae3f07dad77e920d2edf01` on `codex/audit-remediation-ide2-20261025` (base: pinned candidate `4208e31c2fa8c25f88ef0244e0a4d262f3f5970d`) |
| Complete isolated regression | **1267 passed, 0 failed, 2 skipped, 46 warnings, exit 0, 623.51 s** (2026-10-25 01:57:58 IST, Windows, CPython 3.12.10, primary `.venv`) |
| Frontend unit tests | 4 passed / 0 failed (`node --test frontend/tests/deadline.test.mjs`, Node 24.19.0) |
| Production mutations performed | **NONE.** No push, no deploy, no Docker/AWS/SSH/S3/SMTP/VIERP interaction, no real test outside local isolation |
| Production recovery claim | **NONE.** Ingestion remains failed (`invalid_status`, 5 consecutive failures); only IDE 1 + a natural cycle can change that |
| IDE 1 isolation | Preserved: `app/tpo/client.py` and `tests/unit/test_tpo_response_diagnostics.py` are byte-identical to 4208e31 on this branch |

A green suite is not production recovery. Sections 19–22 define what remains.

## 2. Exact starting worktree, branches and SHAs

- New isolated worktree: `C:/Projects/TPO-Watcher-IDE2-Remediation`, branch
  `codex/audit-remediation-ide2-20261025`, created from `4208e31` (pinned
  notification candidate; app bytes of the tested integration `7c32fae`).
- Starting tree clean; HEAD ancestry verified before any edit. No other
  worktree was modified; historical worktrees, reports and untracked user
  files are untouched.
- Baseline before changes: focused suites 89 passed (64 s) at 4208e31.

Local commits on the branch (parent → child):

| Commit | Scope |
| --- | --- |
| `25c6c004742b52c198c20e4ab682849751b17215` | Candidate A — F05/F06/F07 durability + F02/F09 detector code |
| `7c24c7f22d3e4b84c0ff995f493e4821be6e0d51` | Candidate B — F08 log privacy |
| `408c35a9f03a5d74e773ccdf9319da1e4a32c3f1` | Candidate C — F02/F09 dedicated tests |
| `b5ed113cff1ff51d0b39031a0a8925296002e020` | Candidate D1 — F10/F12/F04 |
| `458de0b07568d7548b33f0edc6fe08d6bcbf2a1e` | Candidate D2 — F19/F16 + rebuilt static bundle |
| `f9390f9eb40dd82fc08c77662a5d7cb8d29d88a5` | Candidate E1 — F17 backup/restore hardening |
| `b5a432c76a82d02cfb4c412ac4f652436724035c` | Candidate E2 — F11/F13/F14/F15/F18/F21 docs, pinning, CI |
| `0dfd1a9f155ac34840ae3f07dad77e920d2edf01` | Full-suite fix-up (ledger guard + backup-test mocks) |

Diff vs base: 54 files, +1828/−309 (plus report commits). `app/tpo/client.py`
and IDE 1's test file: zero changes.

## 3. Independently evidenced production state (unchanged by this work)

- Last verified production SHA `7b0797ce4e49b6e3a37764d38ce85155c9ba290f`
  (Scope A diagnostics); prior `647b410f…`; rollback image retained
  (`ed83e825…` tagged `rollback-647b410-20261024`) — all historical receipts.
- **Midnight cycle evidence (25 Oct 00:00 IST, pre-existing sanitized
  observation — preserved verbatim, not re-measured):** natural cycle ran at
  7b0797c; VIERP login succeeded; API HTTP 200 `application/json`; top-level
  object; `status` is a **string**; `company_list` is an array of length **8**;
  `error/data/result/companies/msg/message` absent; rejection category
  **`invalid_status`** (status outside the accepted set); validation and
  reconciliation never reached; consecutive failures 4→5; last-success
  heartbeat unchanged; company/subscriber/delivery fingerprints unchanged; no
  new SENT deliveries. **The actual status string and its meaning remain
  unknown.**
- `SCOPE_A_CONFIGURATION_UNVERIFIED` stands; S3 recovery drill not performed;
  temporary workstation `/32` SSH rule lifecycle unresolved.

## 4–6. Finding-by-finding disposition (mandatory coverage table)

Classification legend per §35. "Tests" name files on this branch; every claim
was executed locally except where marked otherwise.

| ID | Evidence → reproduction → correction/status → tests → commit → remaining risk → approval |
| --- | --- |
| **F01** source ingestion | Midnight evidence above (DOCUMENTED_ONLY). **BLOCKED_BY_PARALLEL_OWNERSHIP** for the classifier (IDE 1). Downstream protection delivered instead: F02 fail-closed reconciliation + F09 identity rejection guarantee no business writes from an untrusted acceptance; source-recovery remains separately releasable (this branch never touches `client.py`). Tests: `test_feed_completeness_gate.py`, `test_observation_identity.py`. Remaining: the predicate fix itself + natural-cycle validation. Approval **APP** for any release. |
| **F02** partial-feed deactivation | Static proof (detector `54–58` + repository `116–174` @4208e31). REPRODUCED_AND_FIXED: `process_fetched_companies(..., observation_complete=False)` is fail-closed by default — a non-empty partial feed now preserves stored companies exactly like an empty feed; absence reconciles only with explicit completeness authority; positive updates unaffected; reactivation via positive evidence preserved. Tests: 7 cases in `tests/unit/test_feed_completeness_gate.py` (partial/complete/empty/positive/reordered/reappear/unknown-state). Commit `25c6c00`+`408c35a`. Remaining: production deactivation is effectively suspended until a completeness-authorized source path exists — deliberate product-safety posture; Scope B independently reviewed (its detector/client approach overlaps IDE 1's file, not adopted). Approval **APP** (data lifecycle). |
| **F03** fixes not deployed | Topology verified (git). ALREADY_FIXED_IN_EXISTING_CANDIDATE (4208e31) + strengthened here (F05–F07) with all seven original stress contracts still green in the full suite. Not deployed. Approval **APP** for release per §19 ordering. |
| **F04** SMTP/ack ambiguity | Architectural boundary (approved at-least-once contract preserved). REPRODUCED_AND_FIXED (surfacing): both ambiguity flavors (ack returns False; ack **raises** after acceptance) now write a durable `notification:v1:ambiguous_acceptance:*` marker, surfaced as a counter in `/health/details`; no auto-resend introduced. Tests: `test_queue_availability_and_readiness.py::test_f04_unacknowledged_acceptance_is_surfaced` + existing `test_f13_fake_acceptance_then_lost_ack_is_at_least_once`. Commit `b5ed113`/`0dfd1a9`. Remaining: inbox-level exactly-once is impossible; operator must investigate markers (runbook in `docs/deployment_worker_compatibility.md`). No approval needed (local); real dispatch unchanged. |
| **F05** company/obligation atomicity | Static gap in S+N (separate commits). REPRODUCED_AND_FIXED: `DatabaseRepository.apply_company_event` commits company upsert + durable obligation (+ event snapshot) in one `BEGIN IMMEDIATE` transaction; detector uses it for NEW/UPDATE/refresh/baseline with a duck-type fallback for legacy test doubles; identical replay creates no duplicate obligation. Tests: 3 cases in `tests/integration/test_notification_event_durability.py` (crash-between → both absent; restart → exactly one; UPDATE + baseline variants). Commit `25c6c00`. Remaining: none known locally. Approval **APP** (persistence blast radius) at release. |
| **F06** FAILED blocks distinct updates | Source-confirmed in both generations. REPRODUCED_AND_FIXED: enqueue now distinguishes events by approved semantics — a distinct later UPDATE requeues a FAILED row **after** appending its terminal outcome (status, snapshot, error) to the new append-only `notification_delivery_outcome_log`; same-event replay stays terminal; NEW lifetime dedupe untouched; no blanket requeue. Tests: `test_f06_failed_update_does_not_block_distinct_later_update` (incl. outcome-log assertions). Commit `25c6c00`. Remaining: audit log is unpruned (append-only by design). Approval **APP**. |
| **F07** deferred event identity/snapshot | Source-confirmed collapse + equivalent-replay duplicate. REPRODUCED_AND_FIXED: (a) obligations persist their event-time snapshot (`pending_notifications.event_snapshot_json`; legacy NULL rows fall back — no invented history); admin fanout renders from that snapshot, not the latest row; (b) delivery event identity uses `CompanyDetector.meaningful_changes` semantics (`_snapshot_event_distinct`) so equivalent representations (zero-seconds times) are replays, never requeues; distinct A→B→C serialize via the approved deferral path, each with its own snapshot. Tests: 4 cases incl. A/B/C sequencing, legacy fallback, equivalent-time replay, identity matrix. Commits `25c6c00`. Remaining: consent/preference policy for an old snapshot is unchanged by design (worker rechecks current consent pre-transport); retaining *every* intermediate event as separate audience emails remains a possible future product decision — not implemented (would be spam-risk). Approval **APP**. |
| **F08** privacy logging | Leak sites verified (email 79/82, limiter 127, auth 51/129/161/168). REPRODUCED_AND_FIXED: provider logs outcome-only (no recipient/subject/provider text); rate-limit logs a bounded key fingerprint, never the raw key (which embeds addresses); auth manager logs fixed categories, drops page URLs/exception text. Tests: canary matrix `tests/unit/test_log_privacy_canaries.py` (success/failure SMTP, rate-limit, auth paths). Commit `7c24c7f`. Remaining: historical logs already written are out of scope; log retention review separately authorized if wanted. |
| **F09** whole-list identity bypass | Static gap (V1 path skips listing-adapter checks). REPRODUCED_AND_FIXED: `_validated_unique_records` runs before any business write — conflicting duplicate IDs reject the **whole observation** (no partial writes), identical duplicates dedupe, blank/whitespace names and unstable identities reject; mirrors the stricter adapter's contract without touching `client.py`/`listing.py`. Tests: 13 parametrized cases in `tests/unit/test_observation_identity.py` (conflict, deadlines, dedupe, blank names, unstable ids). Commit `25c6c00`/`408c35a`. Remaining: oversize-envelope cases remain owned by the client validator (IDE 1 territory). Approval **APP**. |
| **F10** queue coupled to source; sync SMTP | Confirmed (no drain on source failure; smtplib on event loop). REPRODUCED_AND_FIXED: `run_delivery_loop`/`drain_once` give a bounded (≤1 batch/60 s) independent queue lifecycle started by `app.main` alongside the source loop — obligations drain during source outages with all claim/consent/freshness fencing intact and **no new source contact**; SMTP calls (admin + subscriber + fallback) moved off-loop via `asyncio.to_thread`. Tests: `test_f10_queue_drains_while_source_is_unavailable`, `test_f10_drain_tick_survives_worker_failure`, static main-wiring test. Commit `b5ed113`. Remaining: an age-cutoff for undelivered NEWs is a REQUIRES_PRODUCT_DECISION (not invented); stale-NEW suppression relies on existing explicit-inactivity checks. Approval **APP**. |
| **F11** mixed old/new workers | Code proof both generations. DOCUMENTED_DESIGN_LIMITATION + protocol: `docs/deployment_worker_compatibility.md` (single-owner quiesce sequence, lease-expiry recovery, ambiguity handling, rollback-only-to-previous-image, additive-schema rollback notes); fictionally encoded in `tests/integration/test_cross_release_worker_protocol.py` (old numeric ack/release cannot finalize or disturb a token claim; live lease hides rows from a concurrent claimer). Commit `b5a432c`. Remaining: runtime overlap never executed (by design, forbidden). Approval **APP** for any rollout. |
| **F12** health truthfulness | Live-confirmed false-green (24 Oct observation). REPRODUCED_AND_FIXED (additive): `/health` untouched for compatibility; new `/health/details` separates database, watcher freshness, consecutive failures, durable missed-scheduled-start ledger (max-gap + 30 min bound from configured times), admin-drain completion, observation count (empty-success visibility), backlog/terminal delivery stats and ambiguity count. Watcher records `last_scheduled_check_started` (guarded: ledger failure can never block the check itself) and `last_observation_company_count`. Tests: 3 readiness states in `test_queue_availability_and_readiness.py`. Commit `b5ed113`/`0dfd1a9`. Remaining: actual monitoring/alerting wiring (F-OPS below) is OPS-approved configuration. Approval **APP/OPS**. |
| **F13** Scope A config evidence | Historical gap. DOCUMENTED_DESIGN_LIMITATION: status preserved verbatim; `docs/deployment_baseline_capture.md` defines the mandatory secret-free pre/post capture (per-key fingerprints, mount sets, HostConfig semantics, checksums) so future comparisons are complete. No retroactive PASS manufactured. Commit `b5a432c`. Approval: none for the doc; **APP/OPS** for future rollouts. |
| **F14** recovery unproven | Reviewed the prepared verifier (recovery branch `9d26d82`, offline-tested at `7c32fae`): receipt/object SHA pinning, named-profile-only identity, POSIX + private-path enforcement, `mode=ro`+`query_only`+`trusted_schema=OFF`, cleanup verification, listing≠proof — CONFIRMED_STATICALLY_NOT_EXECUTED. `docs/recovery_drill_prerequisites.md` lists the five approvals needed (identity, host, target confirmation, cleanup plan, window). No GetObject performed. Approval **DR**. |
| **F15** SSH /32 lifecycle | Historical. PREPARED_FOR_SEPARATE_AUTHORIZATION: `docs/ssh_access_lifecycle.md` (owner/expiry/alternate-access decisions + exact-rule removal procedure). No AWS access, no record decryption. Approval **OPS**. |
| **F16** frontend truthfulness | Substantiated copy/deadline issues. REPRODUCED_AND_FIXED: RecentDetections re-labeled ("DETECTED / OPPORTUNITY ARCHIVE", "STORED RECORDS — CURRENT AVAILABILITY NOT VERIFIED", historical-sourcing footnote); MonitoringPipeline claims now match the scheduled design (DEDUPLICATED EVENTS / SCHEDULED DISPATCH, fixed-schedule sentence); Hero "Continuous"→"Scheduled"; deadline rendering moved to pure `frontend/src/lib/deadline.ts` — date-only and zone-less values render verbatim with **no invented 23:59:59, no viewer-locale inference**; only explicit zone-aware timestamps may show "(Passed)". Tests: `frontend/tests/deadline.test.mjs` (4/4 via `node --test`). Production bundle rebuilt from this source (hashed assets regenerated, source/bundle parity). Commit `458de0b`. Remaining: no browser-level visual re-verification performed (needs an authorized browser pass). Approval **APP** for shipped frontend. |
| **F17** daily backup / old restore tool | Confirmed gaps. REPRODUCED_AND_FIXED: private-copy chmod failures now **abort** (temp dir 0700 hard-fail, snapshot 0600 enforced); unique non-overwriting S3 keys (short-random suffix + exact-key pre-check refusal); cleanup failures surfaced per-path instead of `ignore_errors` silence; `scripts/restore_test.py` is strictly read-only (`PRAGMA query_only=ON`, **no** `DatabaseRepository` construction — validators must not mutate evidence); systemd README rewritten (private dir, never `/tmp`, read-only checks). Tests: 14 cases in `tests/unit/test_backup_tooling.py` incl. new overwrite-refusal + unique-key assertions; 3 backup-healthcheck tests aligned. Commits `f9390f9`/`0dfd1a9`. Remaining: real permission behavior needs the F20 Linux qualification; upload SSE confirmation still relies on flag + bucket default (role cannot HeadObject — by design). Approval **APP/OPS**. |
| **F18** build/CI reproducibility | Confirmed. CORRECTED LOCALLY where safe: `requirements.txt` pinned to the exact suite-tested versions (with upgrade-gate comment); CI triggers extended to the release branch patterns actually used (`student-auth/**`, `codex/vierp-release-*`, `codex/notification-*`) — local edit, **not pushed**; README env-change guidance fixed (recreate, not restart — env is baked at create); frontend source↔bundle parity restored by rebuilding `app/static` from this commit; `docs/ci_and_supply_chain.md` stale fixed test-count retired. NOT DONE (needs network/decision): Docker base + Caddy digest pinning (proposal noted in doc), npm lock audit gate. Commit `b5a432c`/`458de0b`. Approval **APP/OPS**; push/CI changes need explicit approval. |
| **F19** signup enumeration + delivery claim | Static finding. REPRODUCED_AND_FIXED: default V1 signup now returns one neutral message for new/unverified/verified/inactive accounts (identical bodies asserted), never claims the link was sent, and reflects attempt-only wording; exceptions from transport no longer alter the response; frontend success copy + "VERIFICATION LINK REQUESTED" panel updated to the same truthfulness. Tests: 4 state/transport matrix cases in `tests/integration/test_signup_response_parity.py`; `test_api_v1.py` expectation updated (documented change: old assertion encoded the flaw). Commits `458de0b`. Remaining: account-response compatibility contract for the experimental binding unchanged. Approval **APP**. |
| **F20** Linux/ARM gaps | Probe executed: WSL Ubuntu exists with Python 3.14.4 but **no test dependencies** (pytest/fastapi/httpx absent); installing an uncontrolled parallel environment was out of scope. BLOCKED_BY_MISSING_EVIDENCE / PENDING: the two Windows-skipped native tests (sendmsg isolation, Linux directory-descriptor) and POSIX permission behavior remain unvalidated on this branch; run the pinned suite on an approved disposable Linux/ARM host before release confidence extends to native paths. Approval: separately scoped local qualification. |
| **F21** warnings + stale docs | 46 warnings in the final run (Starlette/httpx deprecations in the newer local venv; historical receipt had 45 — environment drift, no suppression introduced, no asserted CVE). Stale test-count text fixed (F18); runbook/README corrections applied; compatibility upgrade deliberately deferred behind correctness gates. Commit `b5a432c`. Approval **APP** for dependency upgrades. |

Supplemental first-audit items: **empty-feed success semantics** — surfaced via `last_observation_company_count` in `/health/details` (+ F02 preserves records); whether empty-success should count as failure is REQUIRES_PRODUCT_DECISION, not changed. **Application-error-vs-auth classification + numeric status typing (§27/§28)** — BLOCKED_BY_PARALLEL_OWNERSHIP; prepared patch design + test plan in `docs/prepared_source_classification_design.md` for IDE 1 integration (client.py untouched here). **Heartbeat/drain coupling** — check outcome coupling retained (approved stress semantics); drains now independently recorded (`last_admin_drain_complete`) and the queue no longer depends on check success (F10). **Scheduler overlap/missed runs** — missed-start ledger (F12) + recreate-timing rule in README/protocol; no catch-up polling invented. **Monitoring gaps** — `/health/details` provides the signal surface; wiring alerts to it remains OPS (no monitor created). **Container/browser hardening** — DOCUMENTED proposal only (cap-drop/read-only-rootfs can break Chromium sandbox; not applied without a verified alternative). **Local artifacts/worktree hygiene** — untouched by policy; canonical-release-selection procedure added to `docs/deployment_baseline_capture.md` + README recreate guidance. **Snapshot WAL assurance** — daily snapshot uses the sqlite backup API path via the container module (unchanged); release-gate receipt should note the method (one-line doc follow-up listed in §15 unknowns). **Enrollment/auth boundary** — unchanged product boundary; frontend no longer overstates ("verified" labels removed where historical). **SECRET_KEY override** — UNVERIFIED (production env deliberately unread); confirm via sanitized startup-log predicate in the next bounded observation.

## 7–16. Consolidated topic results

7. **Event/transaction correctness.** Company transitions and obligations are atomic (F05); obligations carry event snapshots; deliveries serialize per (user, company, type) with semantic event identity; terminal history audited (F06/F07). All seven approved stress contracts + the full natural-cycle characterization pass unchanged (expectation changes limited to the deactivation steps, which now require explicit fictional completeness authority — the F02 mandate — documented above).
8. **SMTP limits.** At-least-once preserved; both ambiguity flavors durably surfaced; exactly-once inbox delivery remains impossible and unclaimed.
9. **Completeness/identity.** Fail-closed absence; whole-observation rejection of conflicting/blank identities; positive updates and reactivation preserved.
10. **Log privacy.** Canary-verified across provider/limiter/auth paths.
11. **API/frontend/account.** `/health/details` truthful readiness; historical data labeled historical; deadlines never invented; signup state-neutral.
12. **Backup/recovery.** Daily path hardened; restore validation read-only; drill prerequisites documented; drill itself pending approval.
13. **Linux/native.** Pending (probe documented).
14. **CI/build.** Pinned deps, branch-aware CI (local), rebuilt bundle parity, digest pinning proposed.
15. **Migration/rollback.** Additive-only schema (guarded ALTER + CREATE IF NOT EXISTS), idempotent re-runs asserted (compatibility test), old-worker downgrade behavior documented (unused columns remain); rollback image policy pinned to immediately-preceding release.
16. **Regression evidence.** Baseline 89-pass focused green at base; intermediate focused runs per candidate; final complete run **1267/0/2, exit 0** at `0dfd1a9` (Python 3.12.10, Windows). Test-expectation edits were made only where the prior expectation encoded the audited flaw (signup message; deactivation-without-completeness; backup fixed-key mocks) — each documented in §4–6; no assertion was deleted, skipped or weakened.

## 17. Focused/full regression results (summary table)

| Run | Scope | Result |
| --- | --- | --- |
| Baseline @4208e31 | stress + reliability + natural-audit | 89 passed |
| After A | + detector/reconciliation/repository/pipeline/heartbeat | 271 passed |
| After B/C | canaries, completeness, identity | 4 + 19 passed |
| After D1 | queue/readiness + dependent suites | 96 passed |
| After D2 | signup parity + account suites + api_v1 | 50 passed |
| After E1 | backup tooling | 14 passed |
| **Final @0dfd1a9** | **complete isolated suite** | **1267 passed / 0 failed / 2 skipped / 46 warnings / exit 0 / 623.51 s** |
| Frontend | `node --test` | 4 passed / 0 failed |

The two skips remain the inherited Windows-native limitations (sendmsg, Linux
directory-descriptor) — see F20.

## 18. IDE 1 integration dependencies

This branch changes none of IDE 1's files. After IDE 1 lands its
status-marker diagnostic: (1) record its SHA and diff; (2) confirm acceptance
behavior unchanged beyond the classifier; (3) merge onto the then-current
production lineage (see ordering below); (4) run the complete suite against
the merged commit; (5) integrate the prepared §27/§28 patches from
`docs/prepared_source_classification_design.md` at that time. Do not merge
uncompleted IDE 1 work; do not infer status-string semantics; do not deploy a
source adapter without authoritative evidence.

## 19. Proposed release candidates and ordering

1. **Release 1 — source recovery** (gated on IDE 1 + evidence): the approved
   status-classifier commit, rebased onto the current production lineage,
   fictionally reproduced against the observed `invalid_status` predicate,
   full suite green. Never weaken strict acceptance to pass.
2. **Release 2 — this remediation candidate**: rebase/merge
   `codex/audit-remediation-ide2-20261025` onto the post-Recovery-1 SHA (do
   **not** deploy this branch over a newer source fix), re-run the complete
   suite on the merged commit, Linux qualification (F20), then the
   `docs/deployment_worker_compatibility.md` sequence (backup receipt,
   previous-image rollback target, quiesce, verify). The application image
   must be built from the approved merged commit — never from `main`, the
   recovery-tooling integration branch, or untracked files.
3. **Separately**: recovery drill (DR approval), SSH rule removal (OPS),
   monitoring wiring (OPS).

Blast radius (qualitative): Release 2 touches ingestion bookkeeping,
reconciliation gating, queue lifecycle, schema-additive migrations and the
public frontend copy — the deployment protocol's quiesce/verify steps exist
because of exactly that breadth.

## 20. Required future production approvals

Production push/deploy; Docker build/recreate; the Release-1/2 sequence;
recovery drill (GetObject under named identity); SSH-rule removal; any AWS/IAM
change; monitor wiring; real SMTP verification. Previous authorizations do
not carry over.

## 21. Remaining risks and owners

| Risk | Owner/decision |
| --- | --- |
| Ingestion still failing (`invalid_status`, 5 failures); status string unknown | IDE 1 + human evidence decision; no remediation claim until a natural cycle validates + reconciles + heartbeats |
| All fixes local-only | Human release approval per §19 |
| Native Linux paths unvalidated | Human: approve disposable Linux/ARM qualification |
| Exactly-once inbox impossible | Accepted boundary; operator reviews ambiguity markers |
| Empty-success semantics; undelivered-NEW age cutoff | Product decisions, explicitly not invented |
| Monitoring not wired | OPS decision using `/health/details` |
| Base/Caddy image digests unpinned | Future network-enabled change |

## 22. Conditions to declare the project operationally complete

1. IDE 1's evidenced source correction released and a **natural cycle**
   completing validation → reconciliation → success heartbeat.
2. This remediation candidate (rebased onto that lineage) released via the
   worker-compatibility protocol with fresh backup + rollback-image gates.
3. Post-release natural-cycle + queue verification (freshness, no duplicate
   sends, no replay), plus `/health/details` clean.
4. Recovery drill executed under the five documented approvals.
5. SSH rule lifecycle closed; monitoring wired; Linux qualification recorded.

Until all five hold: **not complete** — regardless of any local test count.

— End of report. Worktree `C:/Projects/TPO-Watcher-IDE2-Remediation`, branch
`codex/audit-remediation-ide2-20261025`, final SHA `0dfd1a9f…`. No production
mutations were performed at any point in this session.
