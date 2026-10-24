# TPO-WATCHER — ARM64 CI Execution Result (V2)

**Decision: `ARM64_QUALIFICATION_INCOMPLETE`** — with substantially advanced
evidence: every repository-integrity guard now passes **on the real ARM64
runner**, and the remaining failure is a one-line workflow-input
incompatibility in the pinned `setup-python` action, hit before any dependency
installation or test execution.

## 1. Corrective commit

| Item | Value |
| --- | --- |
| Fix commit | `f758b90d4cfea09af666a47d43637f40245fa9f3` |
| Parent | `eaffca1816042a29477bc4dab4641db3c02d7f24` (docs-only result report of run 1), whose parent is the previously authorized `339347c2ccd5b1daab11ed9c06a09f32ba93a550` |
| Fix diff (complete) | `+          fetch-depth: 0` inside the existing checkout step — one added line, nothing else |
| Preserved | `persist-credentials: false`, pinned action SHAs, runner, permissions, trigger, timeout, all guards and test commands byte-identical |

Note on push content (transparency): the push advanced the remote branch
`339347c… → f758b90…` and necessarily carried the two local-only commits
above the previously pushed HEAD: the docs-only V1 result report (`eaffca1`)
and this one-line fix. The fix commit itself contains exactly the authorized
checkout correction; the runtime guards independently verified the tree.

## 2. The run (the single authorized execution)

| Item | Value |
| --- | --- |
| Run | **40139141559** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40139141559 |
| Trigger | `push` of `f758b90…`, attempt 1, 2026-10-24T22:34:26→37Z (~11 s; public repo ⇒ no billing) |
| Runner | `['ubuntu-24.04-arm']` hosted ARM64 |
| Total runs on branch | 2 (this + the first authorized run; **no retries, no manual dispatch**) |

## 3. Step outcomes

1. Set up job — success.
2. **aarch64 guard — SUCCESS** (`uname -m` = `aarch64` verified natively).
3. Record OS/kernel — success.
4. Checkout (`fetch-depth: 0`, `persist-credentials: false`) — success.
5. **Lineage/tree/application-byte guards — SUCCESS** on the runner: candidate
   `053b152…` proved ancestor with tree `cbddf9e…`, qualification-test commit
   `85646d0…` present, protected-path diff (`app/`, `tests/`,
   `requirements.txt`, `frontend/`, compose, Dockerfile) empty above it.
6. **Set up CPython 3.12 — FAILURE.** Official annotation:
   `Caching for 'false' is not supported` — the pinned
   `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38` (forced to
   Node 24 per GitHub's deprecation notice) rejects the `cache: false` input
   value. No dependency installation occurred.
7–11. Pinned-install record / collection / both native tests / POSIX checks /
   full suite — **skipped** (never executed).

## 4. Qualification requirements status

- Native `aarch64`: **VERIFIED** (guard passed, twice across runs).
- OS/kernel record: executed (content retained in GitHub's log; not exported —
  logs endpoint requires authentication; only official annotations and step
  conclusions were collected).
- Guards: **ALL PASSED** on the runner (fail-closed design vindicated: run 1
  correctly refused to proceed when it could not verify lineage).
- Python/dependency versions, test collection, native tests, POSIX checks,
  suite totals/exit code: **not produced** — blocked at provisioning.
- Dependency/environment issue category: action-input incompatibility
  (`cache: false`), NOT a package-source, pin, or platform failure.

## 5. Minimal prepared correction (NOT applied, NOT pushed)

Remove the `cache: false` line from the `setup-python` step (the action's
default is no caching, which is what the workflow wants):

```yaml
      - name: Set up CPython 3.12
        uses: actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 # v5.4.0
        with:
          python-version: "3.12"
```

Everything else stays byte-identical. The branch is left exactly at
`f758b90…` locally and remotely — no unpushed drift this time. Closing the
gate needs one more single-commit authorization (delete that line → push →
the resulting run executes steps 6–11 for real).

## 6. Hard boundaries honored

One commit, one push (`339347c… → f758b90…`, normal non-force), one
push-triggered run; no rerun/dispatch/retry; no application, dependency-pin,
guard, acceptance, drain, or completeness change; no production/AWS/VIERP/
SMTP/S3/SQLite interaction; public-repo free-tier usage only (~11 s).

## 7. Path forward

Next authorization: *"Push the one-commit `cache: false` removal on
`codex/arm64-qualification-workflow-20261025` and let its run execute."*
Expected next-run profile: steps 1–5 as here, then pinned install
(`pip install -r requirements.txt`), collection (~1,320 expected), both
native tests required passing, POSIX 0700/0600 + `nobody`-denial checks, full
isolated suite (Windows reference: 1,315 P / 0 F / 2 S / 46 W). A green run ⇒
`ARM64_QUALIFIED_READY_FOR_DEPLOYMENT_REVIEW` (consideration only — still no
deployment authorization, no ingestion-recovery claim).

— Application candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136`
(tree `cbddf9e…`). Stopped per mandate.
