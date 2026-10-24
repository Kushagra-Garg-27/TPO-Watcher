# TPO-WATCHER — Final Release Qualification

**Candidate: `053b152b37d8aea8d5b4c74606765cdc73565136`** — branch
`codex/pre-release-integration-20261025` (merge parents: deployed diagnostic
`eb086aee0e26158f7194758dc72ea11c21db609f` + final IDE 2 correction
`7a2217dd3b46781e638b4b64fcc9450f28b518e7`). Qualification window:
2026-10-25, ~02:35–04:0x IST. No production operation performed.

## Final decision: **HOLD_WITH_BLOCKERS**

The candidate passes every locally verifiable gate (A, C–G). Two mandatory
gates remain unverified and block authorization: **native Linux/ARM
qualification (Gate B) is PENDING** — a mandatory target-platform gate — and
the stale-NEW/drain-enablement product decision is open (deployment may
proceed only with the drain left at its default OFF, which is the configured
state). Source ingestion remains unresolved and is **out of scope for this
candidate** by design.

## 1. Gate A — Artifact and configuration identity: VERIFIED

| # | Check | Result |
| --- | --- | --- |
| 1 | Candidate SHA + both merge parents | ✓ `053b152` = merge(eb086ae, 7a2217d) |
| 2 | Follow-up `03bc9818104e9ac1c037323f54ca7a0023b8907d` | ✓ adds only the 227-line verification report; zero app/test/dependency/frontend bytes |
| 3 | Worktree cleanliness | ✓ integration + IDE2 worktrees clean; temp inventory worktree removed |
| 4 | Status classifier unchanged | ✓ `git diff eb086ae 053b152 -- app/tpo/client.py` empty |
| 5 | No source-acceptance relaxation | ✓ `listing.py`/`models.py`/`normalization.py` zero diff vs deployed |
| 6 | No experimental V2 runtime | ✓ `RuntimeMode` default `LEGACY`; only pre-existing enum constants; no compose/env enablement (compose/Dockerfile/Caddyfile/.env.example zero diff) |
| 7 | No recovery tooling mixed in | ✓ `scripts/` contains only backup_sqlite / restore_test / verify_homepage_browser |
| 8 | Frontend source↔bundle consistency | ✓ deterministic rebuild: all Vite content-hash filenames identical; bytes identical after CRLF normalization (Windows checkout artifact); `frontend/src` zero diff since the build commit |
| 9 | Dependency pins | ✓ `requirements.txt` pinned to the suite-tested versions (playwright 1.63.0, fastapi 0.141.1, pydantic 2.13.5, …); runtime-compatibility qualification on the target base image is part of Gate B (pending) |
| 10 | Drain default | ✓ `DELIVERY_DRAIN_INTERVAL_SECONDS = 0.0` (independent drain default-OFF unless explicitly configured) |

## 2. Gate B — Native Linux/ARM qualification: **PENDING (blocker)**

Probe evidence (this workstation): Docker CLI responds but **zero local
images** exist (any run would require a network image pull plus an
uncontrolled dependency install — outside authorized scope); WSL Ubuntu has
Python 3.14.4 with **no pip** and no test dependencies; no ARM64 host is
available locally. No simulated permission fixture was substituted.

Minimum separately approved setup (either):
1. A disposable Linux **ARM64** host/runner (matching production t4g/ARM)
   with Python 3.12, the pinned `requirements.txt` installed from an
   approved source, and the candidate tree — run the isolation bootstrap
   suite; the two Windows-skipped native tests (sendmsg isolation, Linux
   owned-directory-descriptor) must execute; POSIX permission/cleanup and
   SQLite concurrency paths exercised natively; **or**
2. If only Linux x86_64 is approved first: same run explicitly labeled
   **x86_64-only** — it may clear Linux-native behavior but does **not**
   qualify ARM64, and that distinction must be recorded in the release
   authorization.

## 3. Gate C — Migration and rollback safety: VERIFIED

Committed test `tests/integration/test_legacy_schema_migration.py`
(`85646d0641365bcbe6b590bd0e28732340f33e09`): a fixture database built with
the exact deployed-lineage schema (no `event_snapshot_json`, no outcome log)
carrying subscribers+preferences, stored companies, historical SENT, PENDING,
PROCESSING with an unexpired claim, terminal FAILED, a pending admin
obligation and an ambiguity marker.

1. Migration succeeds; all record counts, statuses, heartbeats and baseline
   intact. 2. Second initialization byte-idempotent (schema snapshot equal).
3. Delivery histories untouched (SENT/sent_at preserved). 4. New column
   nullable + outcome table present and empty — legacy obligation snapshots
   stay NULL (**no fabricated history**). 5. New worker claims exactly the
   PENDING row; SENT/PROCESSING(unexpired)/FAILED untouched — no replay.
6. Downgrade characterization: added structures are nullable/separate (old
   INSERTs remain valid; old code ignores them) — rollback leaves them
   unused, matching `docs/deployment_worker_compatibility.md`; the documented
   residual (duplicate admin emails from *overlapping* check processes) is
   excluded by the single-owner quiesce protocol. 7. Rollback preserves the
   database (no drop/downgrade migration exists or is attempted).
8. One characterized legacy edge: a **snapshot-less** delivery row (a
   theoretical pre-fanout-era shape; the deployed worker always writes
   snapshots) converges with exactly one duplicate send under the approved
   at-least-once contract, then settles — asserted deterministically.

## 4. Gate D — Notification dispatch safety: VERIFIED

Focused reruns at `053b152` (50 passed): drain default-OFF disables the loop
without creating any worker (`test_independent_drain_is_default_off`);
scheduled-check drain behavior unchanged; `drain_once` never contacts the
source (code-verified: queue-only path); active claims hidden from concurrent
claimers, expired leases recovered per contract
(`test_f11_*`); release plan forbids overlapping workers
(`docs/deployment_worker_compatibility.md`); accepted-but-unacked (both
False-returning and raising ack) writes ambiguity markers that **only
report** — they neither block nor trigger resends — with the at-least-once
retry contract preserved; consent/verification/cohort/preferences rechecked
immediately pre-transport (existing matrix); stale queued NEW remains an
explicit product decision (no invented cutoff — default-off drain is the
safe posture); F05/F06/F07 focused suites pass.

## 5. Gate E — Source lifecycle and operational truthfulness: VERIFIED

Unknown/incomplete feeds cannot deactivate (fail-closed, 7-case suite);
**no app path supplies `observation_complete=True`** (search-verified);
conflicting duplicate IDs / blank names reject before business writes
(13-case suite); a future valid response still passes through the unchanged
strict validator + Pydantic model (`client.py`/`listing.py`/`models.py`
byte-identical to deployed); the classifier remains observational (no
acceptance change); `/health` 200 ≠ ingestion — `/health/details` separates
`watcher_fresh`/failures/missed-start from liveness, and exposes only
operational counters/timestamps (no records, no identifiers); exact
next-due-instant missed-start evaluation verified across IST schedule
boundaries (09:00 start / 16:00 now, 10:05 start, past-due, absent-ledger
cases); historical UI labels and zone-aware-only deadline rendering verified
by `frontend/tests/deadline.test.mjs` (4/4).

## 6. Gate F — Retention and lifecycle: ANALYZED, decision pending (non-blocking)

New stores hold only opportunity-record JSON and pseudonymous internal IDs —
no emails/tokens/PII (field inventory in `docs/retention_policy_decision.md`).
Obligation snapshots are transient (die with their obligation); delivery-row
snapshots bounded by the unique key; outcome log grows only on
FAILED-superseded events; ambiguity markers persist until reviewed. Fictional
bounded workload (~200 companies, 100 subscribers) ⇒ tens of KB/year for the
outcome log; everything inside the ordinary backup envelope. Open decisions
(outcome-log retention, marker lifecycle, post-completion snapshot
retention) are framed for human review; nothing was pruned. Operator
investigation queries provided that never trigger replay.

## 7. Remaining source-ingestion evidence dependency

`invalid_status` rejections continue (5 consecutive failures; last success
2026-10-23). The deployed classifier reports the marker class only; its
string and business meaning remain unknown. **No parser repair is in this
candidate.** A source-recovery correction must be developed from
authoritative status-marker evidence on the current production ancestry, then
this integration re-reviewed/retested against the resulting SHA (per the
pre-release verification report §9.1). A successful candidate deployment does
NOT constitute ingestion recovery; only a completed natural cycle
(validation → reconciliation → success heartbeat) does.

## 8. Recovery / SSH / monitoring gates (unchanged, separate approvals)

Recovery drill: five documented prerequisites (named identity, private host,
target confirmation, cleanup plan, window) — not performed. SSH `/32` rule:
owner/expiry decisions outstanding (`docs/ssh_access_lifecycle.md`).
Monitoring: `/health/details` provides the signal surface; wiring alerts
remains an OPS decision. `SCOPE_A_CONFIGURATION_UNVERIFIED` remains the
separate historical exception; future releases follow
`docs/deployment_baseline_capture.md`.

## 9. Exact controlled release procedure (candidate-specific)

Preconditions: Gate B satisfied (ARM, or explicitly accepted x86_64-only
limitation), drain decision recorded (default OFF unless explicitly enabled),
retention decision acknowledged.

1. **Preflight records**: current running SHA/image digest + revision label;
   container/volume/Caddy status; `/health` + `/health/details` snapshot;
   bounded DB aggregates (users, verified-active, companies by active flag,
   deliveries by status, pending admin count, PROCESSING leases) with
   before/after fingerprints — captured fresh, never reused from historical
   reports; baseline capture per the doc.
2. **Build identity**: image built from exactly `053b152` (clean checkout,
   `GIT_COMMIT` label = SHA); record image digest; verify no tooling branch
   or untracked files entered the build; static bundle = committed assets.
3. **Fresh transactional backup**: release-gate tooling (or equivalent)
   producing integrity-checked, SHA-256-recorded, 0600/0700 protected copy
   outside the live volume + S3 upload-and-list evidence retained.
4. **Rollback target**: previous application image (current production
   `eb086ae` build) tagged and verified present — never an older release.
5. **Quiesce** (per `docs/deployment_worker_compatibility.md`): stop outside
   a ±30-minute check window; `docker stop tpo-watcher` (grace ≥60s); record
   PROCESSING leases/claim state; no drain under the old image; **no
   overlapping workers at any point**.
6. **Start new image** against the existing volume, Caddy config, env and
   session file — nothing recreated except the watcher container.
7. **Immediate verification**: revision label matches `053b152`; `/health`
   200 with database connected; `/health/details`: schema present
   (migration ran), heartbeat keys unchanged in meaning, backlog counts ≈
   pre-release values, `ambiguous_provider_acceptances` ≈ pre-release;
   homepage + one static asset 200; no JS console errors.
8. **Zero-replay check**: SENT count delta over the release window == 0
   absent new events; no admin pending drain storm; markers unchanged.
9. **Natural-cycle verification**: next scheduled check (00:00/10:00/17:00
   IST) — logs show one start; the known `invalid_status` diagnostic remains
   the expected outcome until a separate source fix; heartbeat
   failure-count continuity (6th consecutive expected — NOT a new failure
   mode); `/health/details` freshness/missed-start fields consistent.
10. **Stop conditions / rollback boundary**: any unexpected SENT delta,
    schema/migration error, container restart loop, Caddy failure, or
    data-aggregate mismatch → stop, investigate read-only; roll back only to
    the preserved `eb086ae` image with the live volume intact and old-worker
    semantics resumed (additive schema stays, unused); rollback itself needs
    explicit authorization. Do not treat `invalid_status` persistence as a
    candidate regression — it predates the release.

## 10. Verified vs unverified summary

| Gate | Status |
| --- | --- |
| A artifact/config identity | VERIFIED |
| B native Linux/ARM | **PENDING — blocker** (probe evidence recorded; minimum setup defined) |
| C migration/rollback | VERIFIED (committed test) |
| D dispatch safety | VERIFIED |
| E source/truthfulness | VERIFIED |
| F retention | ANALYZED; human decisions framed (non-blocking) |
| G release procedure | DEFINED (candidate-specific, above) |

**Final: HOLD_WITH_BLOCKERS** — clear Gate B (and record the drain/retention
decisions) to reach READY_FOR_AUTHORIZATION. No production action was taken
or is authorized by this report.

— Candidate `053b152` (+ verification commits `03bc981`, `85646d0` docs/tests
only). Full-suite receipt unchanged from the pre-release run: 1,315 passed /
0 failed / 2 skipped / 46 warnings, Windows CPython 3.12.10; focused gates
re-executed during this qualification.
