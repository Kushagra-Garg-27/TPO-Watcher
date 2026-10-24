# TPO-WATCHER — Container Build Failure Diagnosis (run 40143717739)

**Verdict: `BUILD_FAILURE_DIAGNOSED` — and the diagnosis supersedes the prior
report's characterization: the Docker image build itself SUCCEEDED. The step
was failed by a post-build inspection command, not by any Dockerfile
instruction, registry, package-resolution, filesystem, architecture or
browser-compatibility issue.**

## 1. How the evidence was obtained

The already-authorized stored git credential (the same session used for the
authorized pushes; token never displayed or persisted) authenticated the
GitHub job-log API for job `121020573585` — HTTP 200, 63,778 bytes retrieved
read-only. No new workflow run, no code change, no production access.

## 2. The actual build outcome (log line evidence)

| Build stage | Log evidence |
| --- | --- |
| `#5 [1/6] FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy@sha256:c257db7f706f31f852740b039dea4f4611418f1a6816c0ceae716dd20d4eddda` | base resolved **by digest** (ARM64 variant per the registry manifest list) — `DONE 21.2s` |
| `#7 [3/6] COPY requirements.txt .` | DONE |
| `#8 [4/6] RUN pip install --no-cache-dir -r requirements.txt` | `Successfully installed … cryptography-50.0.2 … fastapi-0.141.1 … playwright-1.63.0 … pydantic-2.13.5 … pytest-9.1.1 … uvicorn-0.54.0 …` — **all 13 pins + transitive deps resolved from wheels in 11.1 s** — `#8 DONE 11.1s` |
| `#9 [5/6] RUN playwright install chromium` | `Chrome for Testing 153.0.8010.12 (playwright chromium v1243) downloaded … /ms-playwright/chromium-1243`; `FFmpeg (v1011) downloaded … /ms-playwright/ffmpeg-1011`; `Chrome Headless Shell 153.0.8010.12 … /ms-playwright/chromium_headless_shell-1243` — **all three artifacts fetched from `cdn.playwright.dev/…/linux-arm64/…`** — `#9 DONE 8.7s` |
| `#10 [6/6] COPY . .` | DONE 2.0s |
| `#11 exporting to image` | `writing image sha256:bd3b262ce677fee069f7b60fe63fc7d5474b806039ce5668f2f592f939fcf97b done`; `naming to docker.io/library/tpo-watcher-smoke:053b152b37d8aea8d5b4c74606765cdc73565136 done` — **`BUILD_EXIT=0`** |

Every Dockerfile instruction completed. The candidate image exists in the
runner's local store with chromium for `linux-arm64` installed.

## 3. The exact failing operation (not a Dockerfile instruction)

Immediately after `BUILD_EXIT=0`, the step's own diagnostic command failed:

```
2026-10-24T23:41:44.9537861Z BUILD_EXIT=0
2026-10-24T23:41:44.9644919Z Error response from daemon: No such image: mcr.microsoft.com/playwright/python:v1.44.0-jammy
2026-10-24T23:41:44.9672076Z ##[error]Process completed with exit code 1.
```

Cause: the workflow's post-build recording line ran
`docker image inspect mcr.microsoft.com/playwright/python:v1.44.0-jammy …`.
BuildKit resolves and pulls the base image **by digest into its build cache**
and does not materialize the base tag in the local image store, so the
inspect failed — and `set -euo pipefail` correctly turned that non-essential
inspection into a step failure. Steps 9–11 (version record, offline smoke,
cleanup attestation) were skipped as a result. The failure classification is
**workflow-inspection defect**: registry (base pull succeeded), package
resolution (all pins installed from wheels), filesystem, architecture
(native arm64 pull/build) and browser compatibility (all three arm64 browser
artifacts downloaded) are all positively evidenced as working.

## 4. Minimal proposed correction (NOT applied; no retry performed)

In the build step of `.github/workflows/arm64-container-smoke.yml`, record
only images that exist locally and take the base digest from the build
itself:

```bash
          docker build --platform linux/arm64 \
            --build-arg GIT_COMMIT="$CANDIDATE" \
            --build-arg BUILD_DATE="$BUILD_DATE" \
            --iidfile "$RUNNER_TEMP/image-id.txt" \
            -t tpo-watcher-smoke:"$CANDIDATE" \
            "$RUNNER_TEMP/appsrc"
          echo "BUILD_EXIT=0"
          echo "--- built image identity:"
          docker image inspect tpo-watcher-smoke:"$CANDIDATE" \
            --format 'id={{.Id}} os={{.Os}} arch={{.Architecture}}'
          echo "--- base image resolved by digest (see FROM line above); registry manifest list additionally confirms linux/arm64."
```

— i.e., drop the base-tag `docker image inspect` (the base digest is already
recorded in the build's own `FROM …@sha256:c257db7f…` line and by the earlier
registry manifest query), and optionally capture the built image ID via
`--iidfile`. Nothing else changes; all guards, the smoke script and the
boundaries remain as authorized.

## 5. Consequences for the runtime gate

- The **build half of the runtime-image gate is now positively evidenced**:
  the exact candidate builds on native ARM64 inside the real base image with
  `playwright 1.63.0` and its matching `linux-arm64` chromium 153 installed —
  the deployment packet's §3 concern narrows to the smoke half only.
- What remains unexecuted: the offline smoke (in-image versions, SQLite
  migrations in-container, `/health` + `/health/details` via in-process
  client, **actual chromium launch**, idempotent restart). Because the runner
  is disposable, the successfully built image no longer exists; completing
  the gate needs **one separately authorized push of the §4 correction** —
  the resulting run rebuilds (deterministically, from the same immutable
  commit) and executes only the smoke steps.
- The prior verdict `CONTAINER_RUNTIME_INCOMPLETE` stands as the formal gate
  state; this report corrects its failure characterization ("build failed" →
  "build succeeded; post-build inspection failed").

— Candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136` (tree
`cbddf9e…`). Read-only task: no retry, no code change, no production access.
