# TPO-WATCHER — ARM64 Container Build + Offline Smoke Result

**Verdict: `CONTAINER_RUNTIME_INCOMPLETE`**

The single authorized run executed on a genuine ARM64 hosted runner and
passed **every pre-build gate** (aarch64, Docker daemon/buildx availability,
lineage/tree/byte guards, pristine build context from the immutable
application commit) but the **Docker image build itself failed at its first
and only build step** (exit 1, run lifetime 57 s — before pip/chromium could
have completed). The exact build error is written to the GitHub job log,
which the anonymous API cannot download (HTTP 403); with no authenticated
tooling on this workstation the root cause is **undetermined**, so neither
compatibility failure nor transient/provisioning failure can be claimed. No
retry was performed. The candidate `053b152…` is unchanged.

## 1. Workflow commit and run

| Item | Value |
| --- | --- |
| Workflow commit | `0f789132d043bb2f6f4e577bbf5c77a2d071abd8` on `codex/arm64-container-smoke-20261025` (parent `65a8df1…`, the deployment-decision-packet commit; branch contains workflow + reports only — protected-path drift vs `85646d0`: 0 files) |
| Run | **40143717739** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40143717739 (attempt 1, 2026-10-24T23:40:50→23:41:47Z, 57 s) |
| Job | 121020573585, runner labels `['ubuntu-24.04-arm']` |
| Conclusion | failure at step 8 "Build the runtime image once" (steps 9–11 skipped) |
| Runs on branch | 1 — no retries, no manual dispatch |

## 2. Verified before and during the run

- **ARM64**: step 2 `uname -m` = `aarch64` ✓ (native runner, again).
- **Docker availability**: step 3 SUCCESS — `docker version` (client + daemon),
  `docker info`, `docker buildx version` all executed on the hosted ARM64
  runner. Docker WAS available; the mandate's Docker-unavailable stop
  condition did not trigger.
- **Repository guards** (steps 5–7): candidate `053b152…` ancestor with tree
  `cbddf9e…`; qualification commit `85646d0…` present; zero protected-path
  drift; build context prepared as a **detached worktree at exactly
  `053b152`** with tree re-verified and cleanliness asserted — the image was
  to be built from the immutable application commit, not the branch HEAD.
- **Trigger isolation**: the new branch name matches neither `ci.yml` patterns
  nor the full-suite workflow trigger; exactly one run executed; repository
  permissions `contents: read`; no secrets/AWS/VIERP/SMTP references (only
  comment text); `persist-credentials: false`, `fetch-depth: 0`; 40-min
  timeout; `--network none` planned for the smoke phase.

## 3. Build failure evidence and bounded root-cause analysis

- Official annotation: `Process completed with exit code 1` at step 8. The
  step's detailed output is auth-gated (verified: anonymous API job-log
  request → 403; public job page HTML carries annotations only, not step
  logs).
- Run lifetime 57 s: too short for the pip-install + `playwright install
  chromium` + `COPY` sequence to have completed; the failure occurred early
  in the build (base-image resolution/pull, or an early RUN failure).
- **Registry-manifest evidence (read-only query to the intended registry,
  `mcr.microsoft.com`)**: the manifest list for
  `playwright/python:v1.44.0-jammy` contains **both `linux/amd64`
  (sha256:41ddde4d4a4ab…) and `linux/arm64` (sha256:6f05010add062…)** — the
  base image is multi-arch and an ARM64 variant exists. The earlier
  "no-arm64-manifest" hypothesis is therefore **excluded**; the base image is
  not the failing layer by architecture.
- Remaining candidate causes (unresolvable without the log): a transient
  registry/pull error, or an early failure inside `pip install -r
  requirements.txt` (e.g., a wheel-resolution issue on this runner) or at
  base-image extraction. No compatibility conclusion can be drawn — the
  mandated "Chromium must actually launch" bar was never reached.

## 4. What was NOT tested (coverage gap stands)

In-image Python/Playwright versions, chromium presence/launch,
SQLite/migration behavior inside the container, `/health` + `/health/details`
smoke, idempotent restart — all steps 9–10 were skipped. The
runtime-image qualification gap documented in the deployment decision packet
(§3) therefore **remains open**.

## 5. Stop conditions encountered and boundaries

- Stop condition realized: build failure with root cause not extractable
  without authenticated log access → evidence retained, run not retried, no
  workflow/application/dependency modification (per mandate).
- Boundaries honored: one commit, one push, one run; no registry push, no
  artifact upload, no production/AWS/VIERP/SMTP/S3 contact; application
  candidate untouched (`053b152`, tree `cbddf9e`); drain policy, source
  validation and completeness posture unchanged.

## 6. Remaining production release gates

1. **Root-cause read of run 40143717739** — zero-cost, no new execution: the
   authenticated repository owner opens the run URL above and reads step 8's
   docker-build error (or grants approved authenticated tooling to fetch it).
   If transient (registry/pull flake), a separately authorized retry of the
   same workflow is the minimal path; if a real build defect, it requires a
   separate correction + requalification decision (image-spec changes are
   explicitly out of this task's scope).
2. Then the remaining §8 items of the deployment decision packet: policy
   acceptances, controlled-release approval, and the post-release gates
   (backup, rollback image, quiesce, natural-cycle verification).
3. Source recovery and the SSH/monitoring/retention items remain separate,
   unchanged.

— Application candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136`
(tree `cbddf9e…`). Stopped after the single authorized execution per mandate.
