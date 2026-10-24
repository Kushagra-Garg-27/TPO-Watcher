# TPO-WATCHER — Final Deployment Decision Packet

**Verdict: `READY_TO_REQUEST_DEPLOYMENT_APPROVAL`** — with one mandatory
pre-deployment qualification (runtime-image smoke, §3) and three
policies requiring explicit human acceptance (§4) enumerated inside the
approval request. This packet requests; it does not deploy.

## 1. Exact tested application SHA

| Item | Value |
| --- | --- |
| Application candidate | `053b152b37d8aea8d5b4c74606765cdc73565136` (tree `cbddf9ec5ff185b8ba643f4a5801a0e2c07a1bdf`) |
| Deployed status diagnostic (parent) | `eb086aee0e26158f7194758dc72ea11c21db609f` — classifier byte-identical in the candidate (`git diff eb086ae 053b152 -- app/tpo/client.py` empty) |
| Final IDE 2 correction (parent) | `7a2217dd3b46781e638b4b64fcc9450f28b518e7` |
| Approved qualification-test commit | `85646d0641365bcbe6b590bd0e28732340f33e09` (test-only) |
| Branch | `codex/pre-release-integration-20261025` at `053b152` + workflow/docs commits (`9c47be0…` on the qualification branch); application bytes at both branches identical |
| Current last-verified production | `eb086ae…` (deployed 25 Oct 2026 01:35:59 IST) |

## 2. ARM64 qualification evidence (Gate B — CLOSED)

- **Run 40140216755** — https://github.com/Kushagra-Garg-27/TPO-Watcher/actions/runs/40140216755
  — `push` of `9c47be0…`, attempt 1, 2026-10-24T22:49:56→22:55:14Z (5 m 18 s),
  job 121010196504, runner labels `['ubuntu-24.04-arm']`, **conclusion: success**.
- **Genuine aarch64**: step 2's fail-fast guard (`uname -m` = `aarch64`) passed
  natively; the runner label matches.
- **Lineage guards passed on the runner** (step 5): candidate is an ancestor
  with the exact tree hash; qualification-test commit present; zero drift in
  `app/`, `tests/`, `requirements.txt`, `frontend/`, compose, Dockerfile
  above `85646d0`.
- **Python provisioning**: CPython 3.12 (step 6) + exactly the 13 pinned
  requirements installed from the default package source, `pip freeze`
  recorded in the log (step 7).
- **Both previously Windows-skipped native tests executed and passed** (step
  9): `test_native_sendmsg_method_has_installed_guard`,
  `test_linux_owned_descriptor_operations_and_safe_cleanup` — the step fails
  on failure **or skip**, so these were real native passes.
- **Real POSIX checks passed** (step 10): 0700/0600 modes asserted via `stat`;
  the unprivileged `nobody` account verified **denied** the private file and
  directory listing on real Linux.
- **Complete isolated suite: exit code 0** (step 11), including the workflow's
  guard that neither native test appears in the SKIPPED summary.
- Collection completed (step 8). **Exact pytest totals: UNAVAILABLE** — the
  job log requires authenticated download (anonymous API: HTTP 403) and no
  approved authenticated tooling exists on this workstation. Per the packet
  mandate the predicted "≈1,320 passed" is **not** substituted for the actual
  counts; the authenticated owner can read them at the run URL above. What is
  machine-verified from the run: exit code 0 (pytest exits non-zero on any
  failure), both native tests passing, no native-test skips.
- Prior runs (40138553541, 40139141559) were the two authorized
  configuration-failure iterations; no additional CI run is authorized or
  performed.

## 3. Container-runtime compatibility conclusion

**Facts (verified from the candidate tree):** the Dockerfile is byte-identical
to deployed production's — base `mcr.microsoft.com/playwright/python:v1.44.0-jammy`,
`pip install --no-cache-dir -r requirements.txt`, `RUN playwright install
chromium`, `GIT_COMMIT`/`BUILD_DATE` OCI labels. The only runtime-definition
difference vs deployed production is `requirements.txt` now pinning
`playwright==1.63.0` where production resolves `playwright` unpinned at build
time.

**Does the ARM64 CI cover the actual runtime-image combination? NO — with a
strong historical mitigation.** The CI proved the *interpreter + pinned
dependency + suite* combination on Linux aarch64 (Ubuntu 24.04 runner,
CPython 3.12) **outside Docker**. It did not exercise: the jammy-based
Playwright base image, the build-time `playwright install chromium` download
(for pip playwright 1.63.0 on a v1.44 base image), or containerized SQLite/
startup behavior. Mitigation: production images for the current lineage were
built from this same Dockerfile on the ARM64 EC2 host as recently as 25 Oct
(01:35 IST deployment of `eb086ae`), resolving the then-current playwright —
so the base-image/build pattern has working ARM64 precedent; what has never
been image-built is **this exact SHA with the explicit pin**.

**Smallest safe image-build/smoke qualification needed (one-time, no VIERP
contact, no production infrastructure):** build the exact candidate as
`docker build --platform linux/arm64 --build-arg GIT_COMMIT=053b152…` from a
clean checkout; then a disposable smoke container (temp SQLite, no volumes,
no env secrets): image boots `python -m app.main` far enough to run
migrations, `/health` returns 200 on loopback, and `playwright` can launch
its downloaded chromium headlessly (a local `about:blank` page — never
tpo.vierp.in). Record image digest + smoke output in the release receipt.
This is proposed as **the first gate of the deployment preflight** and needs
its own one-line approval (§8). No dependency version is changed by this
packet.

## 4. Accepted/default release policies (explicit human-acceptance flags)

| Policy | State for release | Requires explicit human acceptance? |
| --- | --- | --- |
| Independent delivery drain | **OFF** — `DELIVERY_DRAIN_INTERVAL_SECONDS=0` (default; queue drains only inside scheduled checks) | **YES** — enabling it later is a separate decision accepting current freshness semantics (no approved stale-NEW age threshold) |
| Historical delivery replay | None — no automatic replay of SENT history, no backfill | Accepted as designed; no flag |
| Outcome-log / ambiguity-marker pruning | **None** — append-only; markers persist until reviewed | **YES** — retention decisions pending in `docs/retention_policy_decision.md` (non-blocking for release) |
| Consent/preference checks | Preserved — current-state recheck immediately before every transport (existing contract) | No |
| VIERP status validation | **Unchanged strict acceptance**; the deployed classifier stays observational; no parser repair included | No (unchanged) |
| Partial-feed absence deactivation | **Disabled without authoritative completeness** (fail-closed `observation_complete`; no app path supplies it) — consequence: stale active flags may persist; stored-active ≠ verified availability | **YES** — human acceptance of the conservative data-lifecycle posture (deliberate safety trade) |
| Experimental V2 runtime | Not activated (`RuntimeMode.LEGACY` default; no compose/env enablement) | No |
| Source polling schedule | Unchanged (00:00 / 10:00 / 17:00 IST); no new polling | No |

## 5. Source-recovery sequencing — recommendation

**Option A (recommended): release the tested reliability/data-safety
integration now, explicitly claiming no source recovery.** Rationale from
evidence: ingestion is already failing (`invalid_status`, 5+ consecutive
failures; the 25 Oct 10:00 IST cycle's outcome is **not established** by the
ARM64 result and remains to be read from existing sanitized evidence). On a
failing ingestion path the candidate cannot regress detection — it strictly
improves the safety envelope (atomic durability, fail-closed completeness,
fenced claims, privacy, truthful readiness) and preserves the deployed
classifier byte-for-byte. Waiting for Option B has no evidenced premise: no
status-string semantics are known, and inventing a parser repair is
prohibited.

**Option B (documented alternate):** if a separately justified source
correction becomes ready first, integrate the remediation onto that exact
newer production revision and rerun the affected/full regression gates (the
pre-release verification's §9.1 rule) — never deploy the current candidate
over a newer source fix. Risk-minimizing order given today's evidence is
**A**, with B's merge protocol preserved for when real status-marker evidence
exists.

## 6. Candidate-specific production plan (summary; full procedure in §9 of `reports/TPO_WATCHER_FINAL_RELEASE_QUALIFICATION.md`)

Single-owner release protocol, unchanged: (1) fresh preflight — running
SHA/image digest, `/health`+`/health/details` snapshot, bounded DB aggregate
fingerprints captured fresh, baseline capture per doc; (2) **image-build
smoke per §3 above** + build the release image from exactly `053b152` with
`GIT_COMMIT` label, record digest; (3) fresh transactional protected SQLite
backup (integrity-checked, SHA-256, 0600/0700 copies) + S3 upload-and-list
receipt (upload/list only — never treated as restore proof); (4) verify the
immediately preceding rollback image (current production `eb086ae` build) is
present and tagged; (5) quiesce outside ±30 min of a check window, `docker
stop` grace ≥60 s, record PROCESSING leases and ambiguity markers — **no
old/new worker overlap at any point**; (6) start the new image against the
**preserved live volume, session file, env and Caddy config**; (7) immediate
verification: revision label, `/health` 200, `/health/details` (schema ran,
backlog ≈ pre-release, markers ≈ pre-release), homepage + asset 200;
(8) zero-replay check: no unexplained SENT delta; (9) next natural cycle:
the expected outcome remains the known `invalid_status` diagnostic with
failure-count continuity — **that is not a candidate regression**; no
additional watcher cycle is initiated by this plan; (10) stop conditions: any
unexpected SENT delta, migration error, restart loop, Caddy failure, or
aggregate mismatch → read-only investigation; rollback only to the preserved
previous image with the live volume intact, under its own explicit
authorization.

## 7. Current unresolved operational dependencies (non-deployment)

1. Recovery drill (S3 GetObject under a named identity) — five documented
   prerequisites, separately authorized.
2. Temporary SSH `/32` rule lifecycle — owner/expiry/removal decisions.
3. Monitoring/alert wiring on `/health/details` — OPS decision.
4. Retention policy decisions (outcome log, ambiguity markers) — human
   review pending; no pruning until then.
5. Source recovery — evidence-gated, separate workstream; not a blocker for
   Option A since ingestion is already failing and the classifier is
   preserved.

## 8. Exact necessary deployment approvals (the request this packet enables)

1. **Approve the one-time linux/arm64 image-build + disposable smoke
   qualification of `053b152`** (§3) and record its digest.
2. **Accept the three flagged policies** (§4): drain-OFF default, no
   pruning/retention yet, fail-closed completeness posture.
3. **Approve the controlled production release** of the smoke-qualified
   image per §6 — including the preflight/backup/rollback-image gates, the
   single-owner quiesce, and post-release verification — with rollback
   remaining its own separate authorization.
4. Optionally in parallel: authorize reading the authenticated run log for
   the exact pytest totals to append to the release receipt.

## 9. Verdict

**`READY_TO_REQUEST_DEPLOYMENT_APPROVAL`** — every locally verifiable gate
(A–G) is green, Gate B is closed with native ARM64 evidence, the candidate is
byte-stable, and the residual work is exactly three approvals plus the
runtime-image smoke, all enumerated above with no unknowns assumed. The
packet authorizes nothing by itself: no deployment, no push of application
code, no production access, no additional CI run (branch run count verified
still 3).

— Candidate `053b152…` (tree `cbddf9e…`); production reference `eb086ae…`;
prepared 2026-10-25. End of packet.
