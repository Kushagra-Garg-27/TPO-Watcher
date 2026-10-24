# TPO-WATCHER — ARM64 Qualification Result (Gate B closure attempt)

**Decision: HOLD_WITH_BLOCKERS — `LINUX_ARM64_QUALIFICATION_PENDING`.**

No approved ARM64 (or usable Linux x86_64) environment exists on this
workstation, and the task explicitly forbids creating infrastructure or
installing unapproved dependencies. Per the stopping rule, execution stopped
at the environment gate; everything verifiable locally was verified and is
recorded below. The application SHA is unchanged:
**`053b152b37d8aea8d5b4c74606765cdc73565136`** (tree `cbddf9ec5ff185b8ba643f4a5801a0e2c07a1bdf`).

## 1. Verified locally (this session, 2026-10-25 ~03:20–03:35 IST)

| Required check | Result |
| --- | --- |
| Exact candidate source | ✓ commit `053b152` ("Integrate IDE2 notification/durability remediation over deployed status classifier"), merge parents `eb086aee0e26158f7194758dc72ea11c21db609f` + `7a2217dd3b46781e638b4b64fcc9450f28b518e7`; tree hash `cbddf9e…`; integration worktree clean at lineage HEAD `c621319` (docs/tests only above the candidate) |
| Dependency pins | ✓ `requirements.txt` = the approved tested set: playwright 1.63.0, pydantic 2.13.5, pydantic-settings 2.15.0, httpx 0.28.1, pytest 9.1.1, pytest-asyncio 1.4.0, pytest-mock 3.16.0, tzdata 2026.4, fastapi 0.141.1, uvicorn[standard] 0.54.0, slowapi 0.1.10, jinja2 3.1.6, cryptography 50.0.2 |
| `85646d0` exact scope | ✓ `85646d0641365bcbe6b590bd0e28732340f33e09` (parent `03bc981…`) adds **only** `tests/integration/test_legacy_schema_migration.py` (272 lines) — test-only, no application bytes; it is part of the qualification set |
| Environment inventory | Probed and recorded below — no suitable environment |

## 2. Environment probe evidence (why execution stopped)

| Probe | Result |
| --- | --- |
| Docker daemon | **Not running** (`docker info` fails: cannot connect to `dockerDesktopLinuxEngine`; WSL shows `docker-desktop` Stopped). Even if started: Windows/x86_64 host, **zero cached images**, and any image pull / pip install would be an unapproved network dependency installation |
| Docker buildx / ARM emulation | Builder endpoints in `error` state; no `linux/arm64` platform capability established |
| WSL `Ubuntu` | `x86_64`, Python 3.14.4 (not the 3.12 the suite is qualified on), **no pip**, venv module present, apt amd64 — provisioning it would require unapproved installs, and it is not ARM64 |
| Local wheel cache | The only complete environment is the Windows `.venv` (CPython 3.12.10) — Windows binaries, not transferable to Linux |
| CI | `ubuntu-latest` workflows are x86_64 and no Git push is authorized |

Per the release policy, none of the following were done: starting/provisioning
infrastructure, dependency installation, push/build/deploy/restart, AWS or
VIERP interaction, SMTP/S3 use. `DELIVERY_DRAIN_INTERVAL_SECONDS` remains `0`.

## 3. What the ARM64 run must execute (unchanged expectations)

Against a clean checkout of `053b152` (plus the qualification test commits
`85646d0` lineage — tests/docs only):

1. `python -I -B tests/isolation_bootstrap.py --pytest -q --tb=short`
   (complete suite). Reference receipt on Windows/CPython 3.12.10:
   **1,315 passed / 0 failed / 2 skipped / 46 warnings, exit 0** — on
   Linux/ARM64 the two skips must convert to executed passes:
   - `tests/integration/test_test_isolation.py::test_native_sendmsg_method_has_installed_guard`
     (skip at line 420: "Windows has no native sendmsg; Linux runtime outstanding")
   - `tests/integration/test_test_isolation.py::test_linux_owned_descriptor_operations_and_safe_cleanup`
     (skip at line 492: "Linux owned directory-descriptor runtime outstanding")
2. Native POSIX safeguards with fictional files: real uid/gid ownership,
   0700/0600 mode enforcement, cleanup-on-failure (the backup-tooling and
   isolation suites exercise these paths; on Linux they must hold against
   actual permission semantics, not simulated stat records).
3. SQLite concurrency/claims/migration idempotency: the stress suite's
   deterministic two-connection claim race (F05 contract) and
   `test_legacy_schema_migration.py` (deployed-schema migration, idempotent
   re-init, no replay) must pass natively.
4. Record: OS/kernel, `uname -m` (must be `aarch64` for ARM64 qualification),
   CPython version (3.12.x expected), installed pinned versions, tested SHA,
   totals, warnings, exit code. **x86_64 results, if run, must be recorded as
   not qualifying ARM64.**

## 4. Minimal environment request (to close the gate)

One disposable, isolated **ARM64 (aarch64)** Linux host — e.g., an approved
throwaway AWS Graviton instance, an ARM64 CI runner, or an ARM64
Linux box — with:

- Ubuntu 22.04+/24.04 or Amazon Linux 2023 (aarch64), no production volumes,
  sessions, credentials or network access to VIERP/SMTP/S3;
- CPython **3.12.x** and the ability to install exactly the pinned
  `requirements.txt` from an approved source (no other dependencies);
- read access to a clean checkout of `053b152`;
- authorization to run the isolation bootstrap suite only.

Alternative (partial, explicitly non-qualifying): the same on x86_64 to clear
Linux-native behavior first, recorded as x86_64-only.

## 5. Remaining limitations

- Gate B is the sole remaining mandatory blocker from the final release
  qualification (`reports/TPO_WATCHER_FINAL_RELEASE_QUALIFICATION.md`); the
  drain-enablement and retention product decisions remain open (non-blocking,
  default-off posture preserved).
- A passing ARM64 run permits **consideration of** a separate production
  deployment authorization; it does not itself authorize deployment and does
  not establish source-ingestion recovery (`invalid_status` rejections
  continue until an evidence-gated source fix lands on production ancestry
  and a natural cycle completes validation → reconciliation → heartbeat).

— Application SHA preserved: `053b152b37d8aea8d5b4c74606765cdc73565136`.
No production or infrastructure action was taken.
