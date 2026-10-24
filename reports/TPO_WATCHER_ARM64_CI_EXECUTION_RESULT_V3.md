# TPO-WATCHER — ARM64 CI Execution Result (V3) — Gate B CLOSED

**Final Gate B decision: `ARM64_QUALIFIED_READY_FOR_DEPLOYMENT_REVIEW`**

The complete isolated qualification suite executed natively on a genuine
GitHub-hosted ARM64 runner against the exact integrated candidate and
**succeeded end-to-end**: repository-integrity guards, pinned provisioning,
both previously Windows-skipped native tests (required to pass, not skip),
real cross-user POSIX enforcement, and the full suite with exit code 0 and
the workflow's own no-native-skip guard satisfied. This closes Gate B. It
does **not** authorize a production release and establishes no VIERP
ingestion recovery.

## 1. Exact fix and push

| Item | Value |
| --- | --- |
| Fix commit | `9c47be035a2d01c84a9706d446ccc29feaa3d73d` (parent `7f0546c…`, itself a docs-only report commit above `f758b90…` — disclosed lineage) |
| Fix diff (complete) | one deleted line: `-          cache: false` in the pinned `actions/setup-python` step |
| Metadata validation | Official `action.yml` at the pinned SHA `42375524…` (fetched raw): `cache` supports **only** `pip, pipenv, poetry` — `false` was invalid; pinned `checkout@11bd7190…` confirms `fetch-depth` ("0 indicates all history") and `persist-credentials` as valid boolean inputs; the workflow's only other boolean is `cancel-in-progress: false` (valid concurrency input) |
| Push | `f758b90… → 9c47be0…`, normal non-force, to `codex/arm64-qualification-workflow-20261025` only |
| Pre-push predicates | All 11 verified: clean tree, YAML parse, tree `cbddf9e…`, `85646d0…` ancestor, protected-path diff empty, runner/permissions/trigger/timeout/guards byte-intact, zero secret references, ci.yml untouched, repository still public |

## 2. The qualifying run

| Item | Value |
| --- | --- |
| Run | **40140216755** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40140216755 |
| Trigger | `push` of `9c47be0…`, attempt 1, 2026-10-24T22:49:56Z → 22:55:14Z (**5 m 18 s**, job 121010196504) |
| Runner | GitHub-hosted, labels `['ubuntu-24.04-arm']` — genuine ARM64 (Linux aarch64) |
| Conclusion | **success** — total runs on the branch: 3, each individually authorized; no retries or manual dispatches |

## 3. Step-by-step outcomes (all success)

1. Set up job ✓
2. **Native architecture guard ✓ — `uname -m` = `aarch64` verified on the runner.**
3. Linux distribution/kernel recorded ✓ (retained in the GitHub log; the
   anonymous API exposes step conclusions and annotations only).
4. Checkout (`fetch-depth: 0`, `persist-credentials: false`) ✓
5. **Repository-integrity guards ✓** on the runner: candidate `053b152…` is an
   ancestor with tree `cbddf9e…`; qualification-test commit `85646d0…`
   present; zero drift in `app/`, `tests/`, `requirements.txt`, `frontend/`,
   compose, Dockerfile above it.
6. CPython 3.12 provisioned ✓ (`python-version: "3.12"`)
7. Pinned requirements installed ✓ — exactly the 13 approved pins via
   `pip install -r requirements.txt`; `pip freeze` recorded in the log.
8. Test collection inventory recorded ✓ (collection completed; expected
   ≈1,320 items = 1,317 collected at the candidate + 3 legacy-migration
   cases — derived, exact count log-retained).
9. **Both previously Windows-skipped native tests — EXECUTED AND PASSED ✓**
   (`test_native_sendmsg_method_has_installed_guard`,
   `test_linux_owned_descriptor_operations_and_safe_cleanup`): the step runs
   them explicitly and fails on any failure **or skip**; success proves real
   native execution. No skip conversion occurred.
10. **Real POSIX enforcement ✓** — fictional 0700 directory and 0600 file
    modes asserted via `stat`, then a genuine cross-user check: the
    unprivileged `nobody` account was verified **denied** both the file read
    and the directory listing on real Linux.
11. **Complete isolated suite ✓ — exit code 0** via
    `python -I -B tests/isolation_bootstrap.py --pytest -q --tb=short -rs`,
    including the workflow's guard that **neither native test appears in the
    SKIPPED summary**. SQLite concurrency/claim-fencing (two-connection
    race), transaction/migration idempotency and the legacy-schema migration
    qualification tests are all inside this suite.

**Test totals:** pass/fail/skip/warning numbers are written to GitHub's job
log, which the anonymous API cannot download (HTTP 403); the authenticated
owner can read them at the run URL. What is machine-verifiable from the run
itself: suite **exit code 0** (0 failures — pytest exits non-zero
otherwise), **both native tests executed and passed** (explicit step), **no
native-test skips** (explicit guard), collection completed. Derived, clearly
labeled expectation: ≈1,320 passed / 0 failed / 0 skipped (the two Windows
skips were the only platform skips in the 1,315/0/2 Windows receipt).
Warnings: the run's only annotation is the pre-existing Node-20 deprecation
notice on the pinned actions — informational, identical to the repository's
existing CI.

## 4. Exact tested revisions

- Application candidate: `053b152b37d8aea8d5b4c74606765cdc73565136`
  (tree `cbddf9ec5ff185b8ba643f4a5801a0e2c07a1bdf`) — verified unmodified on
  the runner by step 5's byte-level guard.
- Qualification-test commit: `85646d0641365bcbe6b590bd0e28732340f33e09`
  (legacy-schema migration tests) — verified ancestor, tests executed within
  the suite.
- Workflow-only commits above the candidate: `03bc981`, `85646d0` (test),
  `c621319`, `defadcc`, `1eca06b`, `806d44b`, `339347c`, `eaffca1`, `f758b90`,
  `9c47be0` (+ local docs commits) — none touch application bytes.

## 5. History of the three authorized runs

| Run | Trigger | Outcome |
| --- | --- | --- |
| 40138553541 | shallow checkout | guard failed closed (no tests) — `ARM64_QUALIFICATION_INCOMPLETE` |
| 40139141559 | `fetch-depth: 0` fix | all guards passed; pinned setup-python rejected `cache: false` — `ARM64_QUALIFICATION_INCOMPLETE` |
| **40140216755** | **`cache: false` removal** | **full native qualification — SUCCESS** |

## 6. Boundaries honored

Three single-commit, single-run cycles — nothing more. No application,
dependency, guard, acceptance, drain or completeness change; no history
pruning; no production push/deploy; no AWS/EC2/VIERP/SMTP/S3/production-DB
interaction; public-repo free-tier usage only (~5.5 minutes this run).
Application candidate byte-identical to `053b152` before, during, and after
qualification.

## 7. What this does and does not authorize

Gate B (native ARM64 qualification) is **CLOSED**. The integrated candidate
`053b152` is now qualified on its target platform architecture. A production
deployment remains a separate authorization requiring the full release
procedure in `reports/TPO_WATCHER_FINAL_RELEASE_QUALIFICATION.md` (fresh
backup, rollback image, single-owner quiesce, post-release verification) plus
the still-open product decisions (drain enablement, retention policy).
Source ingestion remains unresolved (`invalid_status` rejections continue);
no parser repair is included, and recovery remains a separate evidence-gated
workstream.

— Application candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136`.
Stopping here per mandate.
