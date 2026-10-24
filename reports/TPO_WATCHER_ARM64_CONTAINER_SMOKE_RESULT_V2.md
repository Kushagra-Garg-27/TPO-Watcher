# TPO-WATCHER — ARM64 Container Smoke Result (V2)

**Verdict: `CONTAINER_RUNTIME_INCOMPLETE`** — with the build half now fully
qualified and the smoke failure root-caused to a **one-line smoke-harness
defect** (Python `sys.path` semantics), not to any application or image
compatibility problem. Candidate `053b152…` unchanged; no retry performed.

## 1. Executed run

| Item | Value |
| --- | --- |
| Pushed workflow commit | `af6b18fd177ab37fd2860eb104c02f9c232800f9` (branch `codex/arm64-container-smoke-20261025` advanced `0f78913… → af6b18f…`, normal non-force; carried two docs-only report commits disclosed previously) |
| Run | **40144718964** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40144718964 (attempt 1, ~100 s, job 121023498300, runner `['ubuntu-24.04-arm']`, `uname -m = aarch64` guard passed) |
| Step outcomes | 1–7 ✓ (incl. Docker availability, lineage/tree guards, pristine `053b152` build context) → **8 Build: SUCCESS** → **9 version record: SUCCESS** → **10 offline smoke: FAILURE** → 11 skipped |
| Runs on branch | 2 — no retries, no dispatch |
| Log evidence | Retrieved with the already-authorized stored credential (73,360 bytes); token never exposed |

## 2. Build qualification (COMPLETE)

- `BUILD_EXIT=0`; **built image ID `sha256:224175675c8fc39b50fcd7b235bccbafb891e886d6a9b2e0b88b623c21616ed0`, `os=linux arch=arm64`** — recorded by the corrected built-image inspection (the V1 defect is gone).
- All six Dockerfile stages DONE; base resolved by digest; `playwright install chromium` fetched `chromium-1243`, `chromium_headless_shell-1243` and `ffmpeg-1011` for `linux-arm64`.
- **In-image Python: 3.10.12** (the `v1.44.0-jammy` base's interpreter) and **playwright: 1.63.0** — recorded from inside the image. Note: the native CI suite ran on CPython 3.12; the image ships 3.10.12 exactly as production's base always has. This is an environment fact to carry into deployment review, not a defect observed at runtime (the application's floors are 3.10-compatible; the suite simply ran on a newer interpreter).

## 3. Smoke failure — exact evidence and root cause

```
== 1. application imports and initialization
Traceback (most call last):
  File "/smoke.py", line 12, in <module>
ModuleNotFoundError: No module named 'app.main'
```

Cause: the smoke script is mounted at `/smoke.py`, so Python sets
`sys.path[0]` to `/` (the script's directory) and never adds the `/app`
WORKDIR — the `app` package therefore is not importable. This is a **smoke
harness defect**, not an application/image defect: the same image
successfully built, installed every pinned dependency, downloaded arm64
chromium, and recorded its versions. The application's runtime behavior was
simply never reached.

**Prepared one-line correction (NOT applied; no retry):** add
`-e PYTHONPATH=/app` to the `docker run` in the smoke step (equivalently,
mount the script inside `/app`). Everything else stays byte-identical. After
that, the smoke's own sequence (SQLite migrations → idempotent re-init →
in-process `/health` + `/health/details` → chromium launch → `about:blank` →
`SMOKE-OK`) executes against the already-proven image.

## 4. Compliance record

- One push (`0f78913… → af6b18f…`), one run; no dispatch/rerun/retry.
- Offline isolation held: `--network none`, disposable `/tmp` SQLite,
  no volumes/secrets/credentials; no VIERP/SMTP/AWS/S3/production contact;
  nothing pushed to any registry; no artifact upload.
- Candidate `053b152b37d8aea8d5b4c74606765cdc73565136` (tree `cbddf9e…`)
  verified unmodified by the runner-side guards; protected-path drift 0;
  drain policy, source validation, completeness posture untouched.
- All twelve pre-push predicates passed before the push (remote at `0f78913`,
  clean tree, unpushed commits workflow/docs-only, fixes verified, trigger
  isolation, 40-min timeout).

## 5. Remaining path

One separately authorized one-line push (`-e PYTHONPATH=/app`) → the next run
executes the smoke for real. A green run yields `CONTAINER_RUNTIME_QUALIFIED`
and closes the final gate in the deployment decision packet; anything else
surfaces a genuine compatibility finding with full log evidence (now
routinely retrievable via the authorized credential). As before: qualification
only — no deployment authorization and no ingestion-recovery claim.

— Application candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136`
(tree `cbddf9e…`). Stopped after the single authorized execution per mandate.
