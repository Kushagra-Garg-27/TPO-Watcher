# TPO-WATCHER privacy exact-tip qualification plan

Prepared locally on 25 October 2026. **PREPARED — NOT EXECUTED ON ARM64.**

> For agentic workers: use `superpowers:executing-plans` for the future execution only after the explicit authorization below. No further application implementation is part of this plan.

**Goal:** qualify the exact privacy candidate on native ARM64 and in its production-style image, without interacting with production.

**Architecture:** one dedicated-branch CI run, one native ARM64 job, two independent clean checkouts of the immutable candidate. The native test checkout receives one provenance-pinned test file; the Docker build checkout receives nothing. Qualification tooling is outside both application checkouts.

**Tech stack:** GitHub-hosted `ubuntu-24.04-arm`, CPython 3.12, unchanged pinned requirements, unchanged Playwright Dockerfile, isolated pytest/JUnit receipts, Docker with networking disabled for runtime smoke.

**Spec:** the user's “PREPARE EXACT-TIP PRIVACY RELEASE QUALIFICATION” request. This document supersedes older production-base statements for this qualification only; historical receipts remain intact.

## 1. Identities and scope gate

| Identity | Exact value |
|---|---|
| Authoritative production | `053b152b37d8aea8d5b4c74606765cdc73565136` |
| Production image / future privacy rollback reference | `sha256:7a39fba511a833da740fcb716af92324435c2e804ce67a762a5045ec683cfce6` |
| Production deployment | 25 October 2026, 13:43:26 IST; supplied authoritative deployment evidence, not re-inspected in this task |
| Application candidate | `e761f8d47d8f3827c685bcecd7aab6adf5afe378` |
| Candidate tree | `33ae3a29c52be62a265086831fb30b9c52fcb1f1` |
| Executable qualification tooling commit | `3dc03c0f1883376ccd84df5c8eb6ac236a8260ea` |
| Dedicated qualification branch | `codex/privacy-exact-tip-qualification-20261025` |

`git merge-base --is-ancestor 053b152 e761f8d` returned **0**. The complete application-candidate commit inventory above production is:

1. `ccfbca7a62f781976dc4711ea47dfd332986478a` — Protect shared runtime logging and watcher exception boundaries.
2. `4bc940d5e15e2571fed5d8bcdb0b9a216ce75e56` — Bound shared log redaction and exercise the real async outage boundary.
3. `e761f8d47d8f3827c685bcecd7aab6adf5afe378` — Redact original overlapping credential spans without leaking fragments.

The complete `053b152..e761f8d` file inventory is **five files, 304 insertions, 10 deletions**:

| File | Scoped change |
|---|---|
| `app/logging_config.py` | Shared bounded privacy formatter; original overlapping credential spans; exceptions/cached traceback/stack suppression; Uvicorn handler propagation |
| `app/main.py` | Only `log_config=None` added to the existing Uvicorn configuration |
| `app/monitoring/watcher.py` | Three raw exception interpolations replaced with fixed messages; unused exception bindings removed |
| `tests/integration/test_shared_logging_privacy.py` | New 21-case real shared-console/Uvicorn and watcher-boundary regressions |
| `tests/integration/test_queue_availability_and_readiness.py` | Correct asynchronous outage fixture and assertions |

**Scope gate: PASS. No unexpected application changes.** Source acceptance/authentication, detector/reconciliation, database/migrations, notification delivery, scheduler implementation, settings, Dockerfile, `.dockerignore`, Compose, requirements, frontend, test isolation bootstrap and conftest are byte-identical to production. The watcher diff changes logging only. Independent drain remains default **OFF**; no automatic pruning is introduced; absence reconciliation remains fail-closed (`observation_complete=False`). No source recovery or parser change is claimed.

The qualification commit descends from the unchanged candidate and existing report-only commit `c308c1625865287baf7f3c145367f3397c81a7bc`. It adds exactly:

- `.github/workflows/privacy-exact-tip-qualification.yml`
- `qualification/privacy_exact_tip/run_suite.py`
- `qualification/privacy_exact_tip/container_smoke.py`
- `qualification/privacy_exact_tip/test_receipt_gate.py`
- `qualification/privacy_exact_tip/test_legacy_schema_migration.py`

This plan is a subsequent report-only addition. **Push the exact tooling commit above if approved; never build a release from the qualification branch.** The workflow builds `e761f8d` explicitly.

## 2. Preserved Windows evidence and local preparation verification

The exact candidate's complete Windows receipt is unchanged:

- **1,336 passed, 0 failed, 2 skipped, 45 deprecation warnings; exit 0; 667.59 seconds.**
- CPython **3.12.10**; all 13 direct requirement pins verified in the original receipt.
- Execution: **25 October 2026, 13:53:39–14:04:48 IST**.
- Private receipt: `C:\Users\kusha\.codex\private\tpo-watcher\audit-privacy-convergence-e761f8d-full.log`.
- Verified raw-file SHA-256: `ab5706bb30a26988b8f22683f647d958f6fc2b0fb25c2fea420a4f0630cc9bf7`.

The two Windows skips are exactly the node IDs named in the previous native qualification workflow:

- `tests/integration/test_test_isolation.py::test_native_sendmsg_method_has_installed_guard`
- `tests/integration/test_test_isolation.py::test_linux_owned_descriptor_operations_and_safe_cleanup`

Older reference evidence is the native workflow/result at `9c47be035a2d01c84a9706d446ccc29feaa3d73d` / run `40140216755`, and successful image smoke at `e59a874335397ce9e64533bc83ff372333ca6882` / run `40145176491` (recorded in commit `7557d3c398fd3295d1a55ef9487c65dab42eaab3`). Those qualify the **older `053b152` candidate only**. No old ARM64 totals are substituted for new measurements; no remote CI evidence was fetched in this task.

Offline verification of this preparation:

| Check | Actual local result |
|---|---|
| All exact shared-logging privacy regressions through the new guarded receipt harness | **21 passed**, exit 0; qualified receipt, zero skips |
| Original qualification-only migration tests plus corrected async outage test | **4 passed**, exit 0 |
| Receipt-gate unit tests | **9 passed**, exit 0 |
| Real Windows native-only negative check | 0 passed / **2 skipped**, pytest exit 0, **qualification exit 1**, `qualified=false`, both native cases false — expected fail-closed behavior |
| Complete candidate plus migration-overlay collection | **1,341 collected**, exit 0 |
| YAML/trigger/permission/action-pin checks | Passed locally |
| Bash syntax | All **9** shell steps passed `bash -n` |
| Embedded Python | **3** heredocs and **2** inline `-c` programs compiled; all four helper Python files parsed |
| Real Uvicorn configuration initialization under the unchanged isolation guard | `config.load()` passed; no server started |
| Protected application/test/build-byte comparison | No difference from `e761f8d` |

The 21-case suite covers shared console output, credential overlaps, oversized records, exception arguments/messages, malformed formatting, cached traceback/stack fields, Uvicorn's actual logger sink and fixed source-diagnostic preservation. The corrected outage uses a normal client constructor with an awaited failing `fetch_companies()`, asserts exactly one fetch, no notification before the fake drain, preserved pending delivery and a failure heartbeat. It no longer accidentally tests an async-constructor error.

The complete 1,336/0/2 Windows run was **preserved, not re-labelled as a new run of the tooling branch**. No native execution, Docker build/container launch, CI execution or production interaction occurred during preparation.

## 3. Prepared native ARM64 procedure

- [ ] After approval only, push the exact tooling commit to the dedicated branch once. Do not create a PR or push to `main`/a release branch.
- [ ] The job rejects a rerun (`github.run_attempt != 1`), requires `uname -m=aarch64`, records OS/kernel, and uses the native ARM64 hosted runner. No QEMU setup exists.
- [ ] Check candidate ancestry/tree and an exact allowlist of qualification-only additions. Protected application, tests, build and frontend files must match the candidate.
- [ ] Make separate detached `native` and `appsrc` worktrees at `e761f8d`. Assert the build worktree is clean.
- [ ] Install the unchanged 13 pinned requirements under CPython 3.12; assert interpreter architecture/version and each installed direct pin.
- [ ] Exercise actual `0700` directory and `0600` file permissions with fictional content. A real `nobody` subprocess must read a public control file, then receive `PermissionError` independently for the private file and directory. Missing sudo/user support or any other error fails; it is not counted as proof of denial.
- [ ] Run the two native node IDs explicitly, then the complete isolated suite. The unchanged candidate bootstrap installs before pytest/application imports; network, source, SMTP and unauthorized subprocess operations remain guarded.
- [ ] Export only bounded JSON receipts from JUnit produced inside the bootstrap's temporary root. Assert totals, zero failures/errors/skips, unique successful native case records, all 21 privacy cases and all three migration cases. Capture the process exit status even when pytest fails; no pipeline may mask it.
- [ ] Verify tracked candidate bytes remain unchanged after tests and the only untracked overlay is the exact approved migration test file.

**Migration overlay provenance:** `tests/integration/test_legacy_schema_migration.py` from `85646d0641365bcbe6b590bd0e28732340f33e09`, preserved verbatim in the qualification folder. Git blob **`98188a60ffa7e71cb163e5987be55b9ce9702914`**, checked before copying into the native test worktree. It adds **three** fictional legacy-schema/idempotency/no-replay cases, not a migration or application change. Native full expected total: **1,338 original cases + 3 = 1,341 passed, 0 failed, 0 skipped**. This is a future pass gate, not an observed ARM64 result.

The complete suite includes existing SQLite concurrency, claim fencing, cross-release worker, durable-event, delivery and migration contracts. The Docker worktree never receives this overlay or the qualification tools.

## 4. Prepared production-style container procedure

- [ ] Verify native runner and Docker daemon architecture. Build once from the pristine `e761f8d` worktree with `--platform linux/arm64` and exact revision label; preserve the Dockerfile and requirements. Record the resolved FROM digest in build output and final image ID/architecture/revision.
- [ ] Create one disposable CI smoke container with network **none**, read-only root, only an ephemeral `/tmp`, dropped capabilities, no-new-privileges, bounded memory/PIDs, and exactly one read-only bind of qualification code. No production mounts, SQLite, session state, Docker socket or application credentials are passed. Inspect and assert effective configuration before start.
- [ ] Override the normal entry point with the smoke probe. Do not run `python -m app.main`, any real watcher/scheduler/drain, an ASGI lifespan, source request or mail transport.
- [ ] Record actual image Python/Playwright versions and verify direct dependency pins. The existing image previously used Python **3.10.12**; this plan records its actual version and does not mislabel the image as Python 3.12.
- [ ] Run **all 21 exact candidate shared-console/Uvicorn privacy tests** in that interpreter using fictional canaries and the unchanged test isolation guard. Require 21 passes, zero skips/errors/failures, exit 0, and save the bounded receipt.
- [ ] Initialize disposable SQLite, require expected tables and `PRAGMA integrity_check=ok`, initialize again and compare schema plus synthetic data dump for idempotency.
- [ ] Build the API and load the real Uvicorn configuration without serving. Require `log_config=None`, disabled access log/server header, and loaded protocols/middleware. Use in-process TestClient checks for `/health` and `/health/details`, connected database, synthetic baseline, and empty notification queues/outcomes.
- [ ] Actually launch installed headless Chromium, open **about:blank only**, record browser version, and close it. Docker networking remains disabled throughout.
- [ ] Require successful process and structured smoke receipts. Always clean up the uniquely named disposable CI container and verify absence using `docker ps -a`; a Docker listing error or leftover container fails the gate. No registry push occurs.

The smoke proves these components on the image interpreter; it is **not a full regression suite inside that interpreter**, a live scheduler test, source recovery evidence or notification-delivery certification. The full regression suite runs separately under native Python 3.12 in the same job. The existing base tag and transitive dependencies are not a hermetic lock; the resolved digest, package versions and final image identity must be retained rather than assuming previous image identity.

## 5. Trigger, permissions, evidence and stopping gates

**One approved CI run can cover both platform requirements.** The single workflow has one sequential job: native guard/full suite, then image build/smoke. Failure of the first gate stops dependent work. Partial receipts cannot qualify the whole candidate.

Verified from local workflow definitions:

- Only push to `codex/privacy-exact-tip-qualification-20261025` triggers this workflow. That branch matches none of the existing `ci.yml` push patterns. No PR, dispatch, scheduled, workflow-run or deployment trigger is added.
- `contents: read` only; no write/OIDC permissions, deployment environments or secret references. Checkout credentials are not persisted. All three Actions are pinned to existing 40-character SHAs. No caches or production credentials are injected.
- No force push, automatic retry, rerun, registry publication, production SSH/AWS/S3 operation or release step exists. Runtime has no external network. Future build/setup downloads are for Actions, the pinned requirements, base image and Chromium installation. The CI host has no newly configured egress firewall: native tests rely on the unchanged isolation guard; the smoke container separately enforces network none.
- The only artifact upload is `evidence/*.json`, containing sanitized numeric/test-result receipts, retained for 14 days. It excludes databases, JUnit failure bodies, temp test trees, session files, environment dumps and raw application output. CI test/build logs contain only this isolated fictional workload.
- Native and container receipts appear in job logs/step summary as well as the artifact. Preserve run ID, qualification head, candidate/tree, native OS/Python, dependency pins, exact totals/exit codes, POSIX proof, image/base digest, image interpreter/browser versions and cleanup attestation. Missing evidence means **INCOMPLETE**, not pass.

Review focus / fail-closed checks:

| Failure class | Required handling / evidence |
|---|---|
| Wrong candidate or extra application changes | Tree/ancestry/protected diff/allowlist fails before test or build |
| Native skips with pytest exit 0 | Per-case JUnit checks and zero-skip/expected-total gates fail; offline negative test verified |
| Missing migration/privacy coverage or duplicate native case | Exact counts and unique native cases fail; fixture unit tests cover each |
| Wrong image, unexpected mount or network access | ARM64/revision/effective configuration assertions before start; runtime network none |
| Lost process status, missing receipt or cleanup uncertainty | Preserve pipeline exit codes; require complete JSON; fail on Docker inspection/listing errors or leftovers |

**Final qualification requires all of:** native full **1,341/0/0**, separate native **2/0/0**, successful POSIX/cross-user proof, unchanged protected bytes, image build success and correct identity, image privacy **21/0/0**, migration/idempotency/API/Uvicorn/Chromium smoke success, and verified cleanup. Any mismatch stops the run; do not weaken a predicate, change candidate bytes, retry or switch runner types automatically.

No production historical log content, credentials, authentication responses or student information was inspected. No actual secret exposure was discovered in this scoped preparation. If genuine exposure evidence appears later, stop and request a separate containment decision; do not rotate credentials or change production logging under qualification authorization.

## 6. Exact next authorization request

**Requested approval, not performed:**

> Authorize one read-only GitHub check that the dedicated target branch is unused, one normal non-force push of qualification commit `3dc03c0f1883376ccd84df5c8eb6ac236a8260ea` to `codex/privacy-exact-tip-qualification-20261025`, and exactly one resulting native ARM64 qualification run covering application candidate `e761f8d47d8f3827c685bcecd7aab6adf5afe378` plus its offline container smoke. Allow dependency/image downloads and retrieval of that run's sanitized receipts. Stop on failure or incomplete evidence; no retry, rerun, other branch update, registry publication or production action is authorized.

After that approval and an unused-target check only, the exact push is:

```powershell
git push origin 3dc03c0f1883376ccd84df5c8eb6ac236a8260ea:refs/heads/codex/privacy-exact-tip-qualification-20261025
```

Stop if the target already exists, local pins differ, repository/runner access is unavailable, or GitHub rejects the run. Repository-side availability and permissions have not been queried under this local-only task.

**Release remains blocked on the new measured qualification and separate deployment approval.** Production remains `053b152` on the supplied authoritative evidence; any future privacy rollback target is that release and its recorded image, **not `eb086ae`**. No build, push, CI run, production restart, VIERP request, SMTP/S3 operation or production data access was performed here.
