# TPO-WATCHER — ARM64 CI Qualification Execution Plan (prepared, NOT executed)

Status: **PREPARED — awaiting explicit one-time authorization to push the
branch. Nothing has been pushed, no workflow has run, and no application byte
changed.**

## 1. Exact identities

| Item | Value |
| --- | --- |
| Application candidate | `053b152b37d8aea8d5b4c74606765cdc73565136` (tree `cbddf9ec5ff185b8ba643f4a5801a0e2c07a1bdf`) |
| Latest verified production | `eb086aee0e26158f7194758dc72ea11c21db609f` (untouched) |
| Qualification tests | `85646d0641365bcbe6b590bd0e28732340f33e09` (`tests/integration/test_legacy_schema_migration.py`, test-only) in the candidate's lineage |
| Qualification branch (local only) | `codex/arm64-qualification-workflow-20261025` — commits above the candidate are workflow/docs/reports only; the workflow itself re-verifies this at run time (ancestry + tree guard + app/tests/frontend/compose/Dockerfile/requirements zero-diff guard) |
| Workflow commit | `806d44b229f827660b4c2605800b0107d878e611` — `.github/workflows/arm64-qualification.yml` only |

## 2. Proposed workflow (summary of `.github/workflows/arm64-qualification.yml`)

- **Runner**: `ubuntu-24.04-arm`, preceded by a fail-fast guard
  `test "$(uname -m)" = "aarch64"` — an ARM64 PASS can never be manufactured
  from a runner label alone.
- **Steps**: (1) architecture guard; (2) record `/etc/os-release` + kernel;
  (3) checkout with `persist-credentials: false`; (4) candidate-identity guard
  (ancestor check, exact tree hash `cbddf9e…`, and zero application/test byte
  drift above the candidate); (5) CPython 3.12 (`cache: false`); (6) `pip
  install -r requirements.txt` (exactly the approved pins; `pip freeze`
  recorded); (7) collection inventory; (8) the two previously-skipped native
  tests executed explicitly —
  `test_native_sendmsg_method_has_installed_guard` and
  `test_linux_owned_descriptor_operations_and_safe_cleanup` — required to
  **pass, not skip**; (9) real POSIX enforcement on fictional files (0700 dir,
  0600 file, modes asserted via `stat`, then `sudo -u nobody` must be **denied**
  both); (10) the complete suite via
  `python -I -B tests/isolation_bootstrap.py --pytest -q --tb=short -rs`,
  exit code captured, and a final guard that neither native test appears in
  the SKIPPED summary.
- **Hard isolation**: `permissions: contents: read`; no environments, no
  secrets (none referenced), no AWS/VIERP/SMTP credentials, no containers, no
  caches, no artifact uploads (bootstrap evidence lives in a temp isolation
  root outside the workspace), `timeout-minutes: 40`, and a `concurrency`
  group that serializes identical qualification jobs (`cancel-in-progress:
  false`).
- **Actions**: SHA-pinned `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`
  (v4.2.2) and `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38`
  (v5.4.0) — byte-identical pins to the repository's existing CI.

## 3. Trigger and privilege analysis

- The workflow triggers **only** on push to
  `codex/arm64-qualification-workflow-20261025`.
- Existing `ci.yml` triggers on `main`, `student-auth/**`,
  `codex/vierp-release-*`, `codex/notification-*` — the qualification branch
  matches **none** of them, so pushing it runs only this workflow.
- `ci.yml` itself: `permissions: contents: read`, test/build/SBOM jobs on
  `ubuntu-latest`, uploads only SBOM JSON artifacts — **no deploy, publish,
  image-push, secret or infrastructure steps exist anywhere in `.github/`**
  (verified by inspection). Dependabot is schedule-based (no push trigger).
- Therefore the one-time push cannot cascade into any privileged or
  deployment workflow.

## 4. ARM64 runner requirements

GitHub-hosted `ubuntu-24.04-arm` (Linux ARM64) runners are a first-class
`runs-on` label. **Cannot be verified from this workstation**: entitlement
depends on repository visibility and plan (public repositories: standard
availability; private repositories: billed Linux-runner minutes, subject to
plan quotas). If the label is unavailable to this repository the job will
queue indefinitely or fail to start — treated as a stop condition below, not
silently retried.

## 5. Python/dependency provisioning (executed only inside the authorized run)

`actions/setup-python` → CPython 3.12.x; `python -m pip install -r
requirements.txt` — exactly the 13 approved pins (playwright 1.63.0,
pydantic 2.13.5, pydantic-settings 2.15.0, httpx 0.28.1, pytest 9.1.1,
pytest-asyncio 1.4.0, pytest-mock 3.16.0, tzdata 2026.4, fastapi 0.141.1,
uvicorn[standard] 0.54.0, slowapi 0.1.10, jinja2 3.1.6, cryptography 50.0.2).
**Pins are never auto-changed**: an install failure is a stop condition for
human review, not a fix-in-place.

## 6. Test commands and acceptance criteria

| Command | Acceptance |
| --- | --- |
| `python -I -B tests/isolation_bootstrap.py --pytest --collect-only -q` | inventory recorded (expected ≈1,320 after qualification-test commits; exact count recorded, no suppression) |
| Bootstrap run of the two native nodeids | `2 passed`, zero skips |
| POSIX check step | modes 0700/0600 asserted; `nobody` denied file read and directory listing |
| `python -I -B tests/isolation_bootstrap.py --pytest -q --tb=short -rs` | **0 failed**, exit code 0; the two native tests absent from SKIPPED; totals + remaining skips + warnings recorded |

Reference receipt (Windows/CPython 3.12.10): 1,315 passed / 0 failed /
2 skipped / 46 warnings — the two skips are exactly the tests this run must
convert to native passes. The suite covers SQLite concurrent claims
(two-connection race), transaction/migration idempotency (incl.
`test_legacy_schema_migration.py`), notification reliability, and enforces
fictional-only transports/no network at the bootstrap layer.

## 7. Network and secret isolation

No secrets are referenced (GitHub exposes none unless declared); checkout
uses `persist-credentials: false`; no environments/containers/caches; the
isolation bootstrap denies unmocked HTTP transports and SMTP at runtime;
nothing contacts VIERP; no production data, volumes or credentials exist in
the repository. The only network use is pip installing the pinned test
requirements during provisioning.

## 8. Cost / billing

Public repository: hosted ARM64 runners are free at standard rates. Private
repository: billed as Linux runner minutes (bounded by `timeout-minutes: 40`;
expected actual use ≈ 10–15 min for install + the ~10-minute suite).
**Visibility/entitlement unverified locally** — the approver should confirm
before authorizing; unexpected queuing is a stop condition.

## 9. Proposed one-time push (exact)

```
git push origin codex/arm64-qualification-workflow-20261025
```

from `C:/Projects/TPO-Watcher-Integration` (HEAD `806d44b` + this report
commit). This creates the branch on `github.com/Kushagra-Garg-27/TPO-Watcher`
and triggers exactly one `ARM64 Qualification (Gate B)` run. **Nothing else
is pushed; no other ref moves.**

## 10. Other workflows that could run on that push

None. `ci.yml`'s branch filters do not match this branch name (verified);
`dependabot.yml` triggers only on schedule. No other workflow files exist.

## 11. Stop conditions

- Runner label unavailable/queued beyond reason (entitlement/billing) →
  cancel, report `LINUX_ARM64_QUALIFICATION_PENDING` with the queue evidence.
- Candidate-identity guard fails (tree drift, application bytes changed) →
  abort; the qualification is invalid, not patched.
- Any pip install failure → abort; pins are reviewed by a human, never
  auto-upgraded.
- Any test failure or native-test skip → record verbatim; no assertion may be
  suppressed to force a pass; failure becomes a documented defect with a
  separately reviewed minimal correction.
- The workflow attempts nothing outside its declared scope; any unexpected
  side effect is treated as a security incident.

## 12. Smallest explicit authorization needed

> "Push `codex/arm64-qualification-workflow-20261025` to origin once to run
> the ARM64 qualification workflow."

That single push is the entire request — it authorizes the branch creation and
one workflow run on GitHub's hosted ARM64 runner; it does not deploy, modify
production, or touch any other ref. Gate B closes only on a green run with
both native tests executed and passing; a green run still does **not**
authorize deployment or claim source-ingestion recovery.

— Prepared 2026-10-25. `DELIVERY_DRAIN_INTERVAL_SECONDS` remains `0`; the
application candidate remains `053b152` byte-for-byte.
