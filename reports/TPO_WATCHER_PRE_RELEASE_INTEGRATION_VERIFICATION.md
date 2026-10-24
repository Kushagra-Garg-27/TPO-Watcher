# TPO-WATCHER — Pre-Release Integration Verification

**Integration SHA: `053b152b37d8aea8d5b4c74606765cdc73565136`** (branch
`codex/pre-release-integration-20261025`, worktree
`C:/Projects/TPO-Watcher-Integration`)

**Verdict: READY_FOR_DEPLOYMENT_REVIEW — source-recovery correction excluded
and still required; no production action taken or authorized by this report.**

## 1. Source and branch ancestry

```
647b410 ── 7b0797c (deployed 24 Oct) ── eb086ae (deployed 25 Oct 01:35 IST,
                                        "Classify rejected source status markers
                                        without changing acceptance")
              │                              │
              │                              └──► 053b152  integration candidate
              │                                     (merge of eb086ae + IDE2 tip)
              └── 152575b ── 4098da9 ── 4208e31 (pinned notification candidate)
                                        └── 25c6c00 … 0dfd1a9 (IDE2 remediation)
                                            └── 918dd3c (docs-only report)
                                            └── 7a2217d (safety-gap fixes; merged in)
```

- Integration merge parents: `eb086aee0e26158f7194758dc72ea11c21db609f` +
  `7a2217dd3b46781e638b4b64fcc9450f28b518e7`. **Conflict-free** (verified:
  IDE 1 touched exactly `app/tpo/client.py` + its diagnostics tests; IDE 2's
  55-file diff has zero file overlap).
- Classifier preserved: `git diff eb086ae 053b152 -- app/tpo/client.py` is
  empty (byte-identical), and the IDE 1 diagnostics tests run green.
- Documentation-only commit verified: `918dd3cae3232bb1bb3ca7736addf8ef002515f6`
  adds only the 196-line remediation report; it is **not** part of the
  integration merge (application/test/build bytes at `053b152` come solely
  from `eb086ae` + `7a2217d`).
- Recovery/backup tooling commits (`7c32fae` lineage) deliberately excluded;
  no untracked files included. Both worktrees clean.

## 2. Verified changed-file scope

`eb086ae → 053b152` equals the reviewed IDE 2 diff (55 files, +1859/−315 as of
`0dfd1a9`, plus the `7a2217d` safety-gap commit) and nothing else:
detector/watcher/repository/sqlite_repository/migrations (durability),
email/rate-limiter/auth-manager (privacy), health server + main + config
(readiness + drain gate), routes_auth + frontend copy/deadline + rebuilt
`app/static` bundle, backup scripts + infra docs, pinned requirements,
CI-release-branch triggers, and 8 new test files (+2 amended).

## 3. Test-inventory reconciliation (collection == execution everywhere)

| Revision | Collected | Decomposition |
| --- | --- | --- |
| `7b0797c` (Scope A) | 1,204 | historical 1,195 P / 7 F / 2 S |
| `4208e31` (candidate) | 1,224 | +20 (notification v1 reliability suite) |
| `7c32fae` (integration w/ tooling) | 1,312 | 1,224 + 88 (recovery_verification + release_backup_gate) = historical **1,310 P / 0 F / 2 S** |
| `0dfd1a9` (IDE2 remediation) | 1,269 | 1,224 + 45 (IDE2) — tooling excluded by design |
| `7a2217d` (IDE2 final tip) | 1,275 | +6 (event-safety + precision tests) |
| `eb086ae` (production) | 1,246 | 1,204 + 42 (IDE 1 diagnostics) |
| **`053b152` (integration)** | **1,317** | 1,204 + 42 + 20 + 51 |

**The earlier 1,310-vs-1,267 difference is fully explained and is neither
regression nor improvement**: the 1,310 lineage included 88 recovery/backup
tooling cases that the remediation branch intentionally omits (no tooling in
the app release), while the remediation branch added its own 45 cases on the
tooling-free candidate base. Changed original-test expectations verified as
safety-preserving: deactivation assertions now require explicit fictional
completeness authority (the F02 contract) with fail-closed preservation also
asserted; the signup assertion now encodes the neutral state-safe message
(the old string encoded the enumeration flaw); backup mocks adapted to the
unique-key contract while keeping size/verification/no-upload assertions and
adding an overwrite-refusal case. Full-cycle characterization retains all
lifecycle steps and counts.

**Complete isolated suite on `053b152`: 1,315 passed / 0 failed / 2 skipped /
46 warnings, exit 0, 608.83 s** (2026-10-25 02:29 IST, Windows, CPython
3.12.10). Focused gate incl. IDE 1 diagnostics + all stress/durability/
completeness/privacy/signup/backup suites: 182 passed. Frontend
`node --test frontend/tests/deadline.test.mjs`: 4/4; `frontend/src` unchanged
since the bundle rebuild commit (source↔bundle parity holds at `053b152`).

## 4. Event-durability guarantees — verified vs unresolved

Verified (code + focused tests at `053b152`):
1. All real V1 company/event writes flow through the atomic
   `apply_company_event` transaction (only duck-typed test doubles use the
   legacy fallback); the production watcher invokes the detector exclusively.
2. Mid-observation interruption converges on replay: a crash at company 2 of
   3 leaves only self-consistent committed units and an identical feed
   replay creates each missing event exactly once, no duplicates
   (`test_mid_observation_interruption_converges_on_replay`).
3. NEW/UPDATE obligations survive interruption (durable `pending_notifications`
   + receipt replay; F05 crash-window tests).
4. Terminal FAILED history is preserved **atomically** with distinct-later-
   UPDATE acceptance (outcome-log INSERT + requeue in one enqueue
   transaction; asserted incl. snapshot content).
5. Concurrent writers converge: in-transaction existence guard (NEW
   obligation only from the transition that actually introduced the row) +
   identical-pending-obligation dedup; serialized `BEGIN IMMEDIATE` units
   (`test_concurrent_new_introductions_converge_to_one_obligation`,
   `test_identical_pending_update_obligation_is_not_duplicated`). Remaining
   exposure: truly simultaneous *distinct* events from overlapping check
   processes could still duplicate **admin** emails — the deployment protocol
   forbids overlapping checkers (single-owner quiesce), documented as the
   control.
6. Event snapshots preserve event identity/content: obligations store
   event-time JSON; student rows requeue/dedupe by approved
   meaningful-change semantics; A→B→C deferral preserves every event with
   its own snapshot and serializes latest-first-complete (documented
   preservation semantics — supersession deliberately **not** implemented to
   avoid silently dropping events).
7. Legacy NULL snapshots fall back to current-row reconstruction without
   fabricating history (tested).
8. Privacy/growth: snapshots contain public opportunity fields only (no
   PII); outcome-log `outcome_note` carries the fixed error categories;
   ambiguity markers are per-delivery bounded. **Retention is currently
   unbounded/append-only by design** — pruning is recorded as an explicit
   future ops decision (not invented here).

Unresolved: none within the authorized local scope.

## 5. Stale-NEW delivery policy — explicit decision

The independent V1 drain is now **default-OFF**
(`DELIVERY_DRAIN_INTERVAL_SECONDS=0.0`): queue progress is coupled to
scheduled checks unless a deployment explicitly opts in with a positive
interval. Rationale: no undelivered-NEW age threshold has been approved, and
under fail-closed completeness a stored-active flag never proves current
availability — an always-on drain could deliver arbitrarily stale NEWs during
long source outages. Opting in requires an explicit deployment decision
accepting current freshness semantics (stored-active + consent/preference
checks only). Cancellation-during-transport verified safe: cancelling the
worker while SMTP runs in its thread leaves an unacknowledged claim that
recovers through lease expiry per the approved at-least-once contract
(`test_cancellation_during_offloop_transport_leaves_recoverable_claim`).
Ambiguity markers **report only** — they neither prevent nor trigger retries;
the at-least-once retry contract (possible duplicate after
accepted-but-unacked) is unchanged and explicitly preserved.

## 6. Completeness and identity guarantees

- `observation_complete` has **no True supplier anywhere in application
  code** — production V1 absence reconciliation is fail-closed by
  construction. Eight items, HTTP 200, or schema validity can never
  authorize deactivation; only the explicit kwarg can, and nothing supplies
  it (verified by search at `053b152`).
- Conflicting duplicate identities and blank/whitespace names reject the
  entire observation before any business write (13 parametrized cases).
- Documented consequence (also user-facing via the relabeled frontend):
  conservative absence handling **retains stale active flags** until a
  completeness-authorized observation exists; a stored active flag is not
  verified availability. Source acceptance semantics and the deployed
  classifier are untouched (`invalid_status` rejection stands; no
  speculative parser repair was added).

## 7. Logging and API privacy

- Provider/rate-limiter/auth boundaries log outcomes and fixed categories
  only; canary tests cover success, provider-failure, rate-limit and auth
  paths. Rate-limit fingerprints are now **HMAC-keyed with the deployment
  secret** (bounded 12 hex): correlatable within one deployment, not a
  globally linkable hash of an address. Residual: within-deployment
  linkability is intentional for abuse response; log-retention review
  remains separately authorized.
- `/health/details` (unauthenticated, like `/health`) exposes operational
  metadata only — timestamps, counters, statuses, no PII, no company rows,
  no subscriber data; missed-check evaluation now computes the **exact next
  due instant** from the actual configured schedule (00:00/10:00/17:00 IST +
  30-min slack) rather than a coarse max-gap (precision tests added,
  including between-checks and past-due boundaries). Aggregate volume
  visibility (delivery counts) matches what `/health` already published.
- Signup: one neutral status/message pair across account and transport
  states (tested incl. SMTP failure/rejection).
- Frontend: zone-aware-only deadline inference with ambiguous/date-only
  fixtures verbatim; historical labeling corrected.
- Daily S3 backups: unique random key + exact-key pre-check is **not**
  atomic conditional creation — documented verbatim in
  `infra/systemd/README.md`; upload-and-list remains a weaker assurance level
  than an independently authorized restore drill, and the two are not
  conflated anywhere.

## 8. Linux/runtime qualification status

**NOT QUALIFIED — pending.** The two Windows skips (native `sendmsg`
isolation, Linux owned-directory-descriptor) remain skips; POSIX
permission/cleanup behavior of the backup tooling is simulated only. Probe
evidence: WSL Ubuntu present (Python 3.14.4) but without pytest/fastapi/httpx;
installing an uncontrolled parallel environment was out of authorized scope.
Required before release confidence extends to native paths: run this exact
pinned suite on an approved disposable Linux/ARM host (no production
volume/session/credentials).

## 9. Remaining recovery and operations gates

1. **Source recovery** (separate, evidence-gated): the deployed classifier
   reports `invalid_status` but does not repair ingestion (5 consecutive
   failures stand). Any parser correction requires authoritative
   status-marker evidence; if developed, this integration must be
   reviewed/retested against the resulting new production ancestry.
2. Pre-release: fresh transactional backup receipt + protected copy;
   immediately-preceding rollback image (`eb086ae` image once this ships);
   the `docs/deployment_worker_compatibility.md` single-owner quiesce
   sequence (no overlapping old/new workers); baseline capture per
   `docs/deployment_baseline_capture.md`.
3. Recovery drill (5 documented approvals), SSH-rule lifecycle closure,
   monitoring wiring on `/health/details`, outcome-log/ambiguity-marker
   retention policy.

## 10. Deployment-review readiness

The candidate is **ready for deployment review**: conflict-free integration,
deployed classifier preserved byte-identically, all safety-critical semantics
verified or explicitly gated (default-off drain; fail-closed completeness;
atomic durability; privacy canaries; truthful readiness), full suite green
with reconciled inventory, frontend parity, and every remaining decision
surfaced rather than assumed. It is **not** a source recovery and confers no
production-recovery claim.

**Minimal next approval requests (exact):**
1. Approve deployment review of `053b152` per the worker-compatibility
   protocol (production push/build/recreate + pre/post gates).
2. Approve a disposable Linux/ARM qualification run of this pinned suite.
3. Decide the stale-NEW age policy (or explicitly accept current semantics
   and set `DELIVERY_DRAIN_INTERVAL_SECONDS`).
4. Separately: authorize the recovery drill prerequisites and the SSH-rule
   lifecycle owner/expiry.

— No production operation, VIERP contact, live email, S3 GetObject, AWS or
security-group change was performed at any point in this verification.
