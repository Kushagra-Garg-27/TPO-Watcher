# Stage C2 — Native ARM64 Qualification Plan (PREPARED, NOT EXECUTED)

**Status:** Prepared 2026-10-26 (final-safety-gates revision). Execution
requires a separate, explicit one-time human authorization: a single
`workflow_dispatch` of `operator_mfa_arm64_qualification.yml` on the
dedicated tooling branch. Nothing here touches production, VIERP, or the
qualified watcher application (used only as an unmodified, read-only
interoperability reference).

## Scope and identity gates

1. **Host class:** genuine `aarch64` Linux — GitHub `ubuntu-24.04-arm`
   runner (`uname -m` must report `aarch64`; the workflow aborts otherwise).
2. **Tool revision pinned:** the workflow records HEAD commit SHA, HEAD
   tree SHA, and the `operator_tool/` subtree SHA; an optional dispatch
   input `expected_tooling_sha` hard-pins the exact tip.
3. **Application invariance:** the workflow asserts
   `git diff f4def96 HEAD -- app/ tests/ Dockerfile requirements.txt
   docker-compose.yml .dockerignore` is EMPTY, and runs
   `operator_tool/verify_production_build_exclusion.py`.

## Dependency gates

4. CPython 3.12 (pinned actions/setup-python, SHA-pinned to v5.4.0).
5. `pip install -r operator_tool/requirements.txt` resolves to exactly
   `playwright==1.63.0`; the app's pinned `requirements.txt` is installed
   ONLY so the unmodified qualified modules can be imported read-only for
   interop; `pip freeze` recorded numerically.
6. `playwright install --with-deps chromium` fetches the ARM64 build.

## Offline suites (all must pass with ZERO failures, ZERO skips,
## ZERO platform deferrals — Linux deferrals are suite failures)

7. `test_lock_deterministic.py` — retired-design steal-race proof,
   kernel-lock immunity at the exact §5 interleavings (12 executed on
   Linux incl. B1b/B6/B7b which defer on Windows).
8. `test_lock_concurrency.py` — 10-way/6-way cross-process races,
   live-holder preservation, killed-holder kernel auto-release.
9. `test_publication_faults.py` — fault injection at every publication
   boundary; on ARM64 the two full-tool browser checks (F10/F11) execute.
10. `test_adversarial_mfa_operator.py` — real Chromium vs fictional
    loopback portal; on Linux the deferred symlink scenarios (18/18b) and
    the literal-0600 assertion (19b) MUST execute and pass.
11. `test_linux_fixtures.py` — volume-permission model on a real Linux
    kernel: root-owned 0755 denial for uid 1000, 0700/0600 literals,
    symlink rejections, flock semantics, millisecond mtime precision.
12. `test_watcher_interop.py` — qualified `AuthManager`/`PlacementWatcher`
    contracts (read-only import; runtime URL patching only).
13. `test_stage_b_exclusive_placement.py` — exclusive placement/rollback
    rehearsal under GNU coreutils.

## POSIX-specific proofs (must be evidenced on ARM64 Linux)

14. Published state file mode literally `0600`; staging dir `0700`.
15. Symlinked destination AND ancestor rejected for real.
16. `fcntl.flock` kernel-managed locking under genuine concurrent load;
    advisory-lock payload rewrite under an active holder ignored.
17. Read-only lock directory fails closed.
18. `fsync` durability contract on the staged file (fatal on failure).

## Container behavior proofs

19. Image builds for `linux/arm64` from `operator_tool/Dockerfile` with
    build context `operator_tool/` only (full isolation from watcher
    sources, reports, credentials).
20. Image gates: `Architecture=arm64`, `User=operator`, no exposed ports,
    no secret-looking environment entries.
21. Fictional browser suites run inside the container with
    `--network none` (loopback-only portal; zero external egress).
22. Non-root identity proof (`uid=1000(operator)`) inside the container.
23. No orphan Chromium processes after the container suites (`pgrep`).
24. One-shot semantics: `restart: "no"`, no daemon, no CDP/debug port,
    no Docker socket or privileged mounts anywhere in the run commands.

## Receipt requirements

25. Sanitized numerical receipts only in the job summary (suite counts,
    pinned-package count, container check counts, sha LENGTHS) — no URLs,
    paths under `/home`, tokens, or portal addresses.
26. Verdict string on success: `STAGE_C2_OPERATOR_TOOL_ARM64_QUALIFIED`
    (recorded in the run report; the workflow itself gates on exit codes).
    This verdict authorizes NOTHING beyond tool qualification; live use
    remains gated on the account/institutional authorizations, the
    one-time session-directory ownership setup, the Stage B placement,
    and the deployment decisions.

## Explicit non-goals

- No live VIERP navigation, login, MFA, or OTP of any kind.
- No production host access, no Compose override placement.
- No rebuild, deployment, or mutation of the qualified application.
- No push or workflow dispatch by the preparing agent.
