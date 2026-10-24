# TPO-WATCHER — ARM64 CI Execution Result

**Decision: `ARM64_QUALIFICATION_INCOMPLETE`**

The single authorized execution ran on a genuine GitHub-hosted ARM64 runner
and failed at the workflow's own pre-test lineage guard, before any test
executed. The failure is workflow infrastructure (shallow checkout), not a
test failure and not an application defect. The candidate
`053b152b37d8aea8d5b4c74606765cdc73565136` is unchanged and unqualified — no
ARM64 PASS exists and none is claimed.

## 1. Executed run (the one authorized execution)

| Item | Value |
| --- | --- |
| Run | **40138553541** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40138553541 |
| Workflow / job | ARM64 Qualification (Gate B) / job 121005198962 "Native aarch64 isolated suite" |
| Trigger | `push` to `codex/arm64-qualification-workflow-20261025`, SHA `339347c2ccd5b1daab11ed9c06a09f32ba93a550`, attempt 1 |
| Created → updated | 2026-10-24T22:26:18Z → 22:26:27Z (~9 s; public repo ⇒ **no billing**) |
| Runner | `GitHub Actions 1000000203`, labels `['ubuntu-24.04-arm']` |
| Conclusion | **failure** (step 5); steps 6–11 skipped |

## 2. Step outcomes

1. Set up job — success.
2. **"Fail fast unless the runner is genuinely aarch64" — SUCCESS** (`uname -m`
   = `aarch64` verified on the hosted runner; the platform requirement itself
   is real and reachable).
3. Record Linux distribution/kernel — success (log content retained by
   GitHub; not extracted — logs endpoint requires authentication, so only the
   official check annotations and step conclusions were collected).
4. Checkout — success (shallow, see root cause).
5. **"Verify exact candidate lineage and unchanged application bytes" —
   FAILURE.** Official annotation message: `candidate
   053b152b37d8aea8d5b4c74606765cdc73565136 is not an ancestor of this
   branch` (exit 1).
6–11. Setup Python / pinned installs / collection / native tests / POSIX
   checks / full suite — **skipped** (never executed).

## 3. Root cause (evidence-backed, locally reproduced)

`actions/checkout@v4` defaults to **`fetch-depth: 1`** — a shallow clone of
the tip commit only. The workflow's guards (`git merge-base --is-ancestor
053b152…`/`85646d0… HEAD`) therefore could not reach the older commits and
correctly failed closed. The guard logic itself was verified correct against
the full local repository (same expressions pass locally and the rejection
demo fires on real drift); the omission is `fetch-depth: 0` on the checkout
step of `.github/workflows/arm64-qualification.yml` (lines 40–43).

## 4. Minimal prepared correction (NOT applied, NOT pushed)

Per the mandate ("retain the evidence and stop; do not silently change
application code, dependency pins or workflow guards"), the branch is left
untouched at `339347c` — remotely and locally identical to the authorized
push. The separately approvable workflow-only fix is exactly:

```yaml
      - name: Checkout qualification source
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0            # full history for the lineage guards
          persist-credentials: false
```

Nothing else changes: same guards, same pins, same runner, same acceptance
criteria. Because the branch already exists on the remote, closing the gate
needs **one new authorization** to push that one-commit fix — the resulting
push-triggered run would be the next (and only) qualification execution.

## 5. Phase 1 preflight record (all ten items passed before the push)

1. Local HEAD = `339347c…` ✓. 2. Clean worktree ✓. 3. Candidate tree =
`cbddf9ec5ff185b8ba643f4a5801a0e2c07a1bdf` ✓. 4. `85646d0…` ancestor ✓.
5. Protected-path diff `85646d0..HEAD` empty ✓. 6. Trigger scoped to the
dedicated branch ✓. 7. `contents: read`, SHA-pinned actions,
`persist-credentials: false` ✓. 8. Runner `ubuntu-24.04-arm` ✓ (and it
actually allocated). 9. No deploy/AWS/VIERP/SMTP/publish steps ✓.
10. Repository confirmed **public** via the anonymous GitHub API
(`"private": false`) ⇒ hosted ARM64 runners free; zero billing exposure
confirmed post-run (~9 s).

## 6. Non-sensitive evidence ledger

Run ID/link above; branch + SHA above; runner architecture **aarch64**
(proven by step 2's guard passing) with `ubuntu-24.04-arm` label; OS/kernel
details retained in the GitHub log (not exported); guard result: fail-closed
at step 5; test inventory: **none collected**; native test outcomes: **not
executed**; suite totals/exit code: **not produced**; failure category:
workflow-infrastructure (shallow clone) — `ARM64_QUALIFICATION_INCOMPLETE`,
not `ARM64_QUALIFICATION_FAILED` (no test failed and no platform behavior
was measured); billing limitation: none (public repository, seconds-long
run). The Node-20 deprecation warning on the pinned checkout is informational
and matches the repository's existing CI.

## 7. Hard boundaries honored

Exactly one push (branch creation, non-force) and exactly one
push-triggered run; no re-run, no workflow-dispatch, no additional branches/
tags/PRs; no `main`/production ref moved; no application/dependency/guard
change; drain stays `0`; deployed diagnostic and fail-closed completeness
untouched; no VIERP/SMTP/S3/AWS/production interaction.

## 8. Path forward

1. Approve the one-commit workflow fix (§4) → push → the new run executes
   steps 6–11 for real (native tests, POSIX enforcement, full suite).
2. A green run ⇒ `ARM64_QUALIFIED_READY_FOR_DEPLOYMENT_REVIEW` (consideration
   only — still no deployment authorization, no ingestion-recovery claim).

— Application candidate unchanged: `053b152b37d8aea8d5b4c74606765cdc73565136`
(tree `cbddf9e…`). Branch `codex/arm64-qualification-workflow-20261025` at
`339347c…` both locally and on origin. Execution stopped per mandate.
