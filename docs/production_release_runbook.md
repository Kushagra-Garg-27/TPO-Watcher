# Release procedure and acceptance record

This is an unexecuted procedure. It grants no deployment, source access, SMTP,
cloud spending, private data access or Git publication authority. The final
source manifest, native image digest, owner approvals and fresh target evidence
must be attached before execution. The example runtime configuration deliberately
fails validation until its placeholders and shutdown assertions are verified.

## Release identity and validation

The integration branch is `codex/tpo-final-release-20261022`, based on Student
Authentication PR #11 at `eae68680a51513ed422e642bf417166aadfba6a9`. PR #11 remains
open and unmerged. The worktree is an unpublished candidate; its base SHA alone
does not identify the changes. Use the final file manifest with the base SHA.

Run `scripts/run_guarded_regression.mjs` with the verified Python executable and
a new external evidence directory. It records exact collected nodes, per-module
fresh-process execution, logs, outcomes, source hashes and changes during the
run. The frozen 19 v1 diagnostic failures remain executed and individually
identified in `tests/historical-c1-failures.json`. A green active-contract result
is not a claim that every historical diagnostic passes. Two platform-specific
skips must be interpreted against native Linux verification.

On the separately authorized native ARM64 builder, verify `uname -m`, Docker
server architecture, available space and the exact source manifest. Build with:

```text
docker build --platform linux/arm64 --target runtime -f Dockerfile.release
  --build-arg SOURCE_MANIFEST_SHA256=<reviewed-manifest-sha256>
  -t <approved-candidate-tag> .
docker build --platform linux/arm64 --target verification -f Dockerfile.release
  --build-arg SOURCE_MANIFEST_SHA256=<reviewed-manifest-sha256>
  -t <approved-verification-tag> .
```

The verification image's entrypoint is the unchanged isolation bootstrap. Run
its self-test, collect every node, then run one module per fresh disposable
container with `--network none`, no host credentials or data mounts, and only
fictional fixtures. Preserve stdout and exact container/image identities. The
runtime target contains the production entrypoint and Chromium dependencies;
the earlier inert target is not a substitute. Record image IDs, platform,
dependency hashes, installed browser version and software inventory. Do not
invent a registry digest for an image that has not been published. The final
native runtime must also prove its read-only root, non-root configured UID,
browser tmpfs permissions, health endpoint, shutdown and restart behavior.

## Fresh target discovery

Re-identify instance `i-03328ee0e60347254`, region `ap-south-1`, public routing,
architecture, repository path, Docker/Compose version, Caddy configuration,
network and actual volume/database filename. Historical IP `65.0.66.80` and
`/home/ec2-user/TPO-Watcher` are hints, not present-day proof. Record current
container images, mounts, restart policies, systemd/cron/Compose startup paths,
SQLite integrity/FKs/schema/WAL and every possible writer or sender. Do not
print environment secrets, user rows, tokens or credentials. Public health and
historical SENT counts do not prove v2 is running.

## Owner checkpoint contents

One release approval must bind the reviewed source manifest and native image,
instance and exact existing volume, current schema and backup, migration plan,
source access/scope evidence, key custodians and recovery volumes, service
exclusion, cutover window, acceptance checks and forward-recovery procedure.
No-send deployment and physical SMTP activation are separate decisions. A pilot
must bind `public_origin` to the exact approved HTTPS origin. Account origin
checks and action links use this configured value even when Caddy forwards over
HTTP; client-supplied forwarding headers cannot redefine it. Verify browser
account mutations through the real Caddy route before pilot activation. A pilot
also requires sender ownership, injected transport credentials, named recipient
allowlist, enrollment/destination/consent evidence, message classes, expiry,
per-recipient/total limits and pacing. Source-local dates and compensation units
remain unverified unless separate evidence establishes them. No institutional
email naming rule establishes enrollment.

## Approved cutover sequence

1. Verify the approved target and file/image hashes again. Put the site into
   the reviewed maintenance state through the existing Caddy. Keep Caddy and
   its data intact; do not use `compose down`, volume prune or an empty volume.
2. Disable automatic restart and all scheduler/service paths for the legacy
   writer, admin, subscriber and account senders. Stop and verify all processes.
   Record unresolved provider calls separately; process/controller death alone
   does not establish SMTP acceptance or physical END.
3. Provision owner-only persistent custody and independent checkpoint mounts,
   and separate 32-byte wrapping-key files. Provide a distinct high-entropy web
   session secret. Never store these in source, image, logs or browser bundles.
4. With all writers quiescent, complete the current WAL checkpoint and verify
   no outstanding writer handles before computing the main database hash.
   Use the same canonical in-container path `/var/lib/tpo/<actual-filename>` for
   every maintenance/runtime invocation. Namespace identity is path-bound.
5. Run the `production_maintenance migrate` procedure documented in
   `production_custody_recovery.md`, through the reviewed release image with
   explicit entrypoint override. It creates a new verified recovery artifact,
   installs the additive schema/account policy and enrolls no-send readiness.
   Never run migration implicitly at application startup.
6. Run maintenance `verify` with the new current file hash. Record that hash in
   the owner-installed runtime config. Preserve checkpoint and vault identities
   separately from the application backup. A restart after database writes
   requires fresh quiescent identity verification and a refreshed hash; automatic
   restart is intentionally disabled.
7. Start only `watcher` from `deploy/compose.release.yml` with an exact approved
   image, verified external network/volume, verified non-root UID/GID and mount
   paths. Configure `pilot: null` and `smtp: null`. Never combine the legacy
   Compose file with the release file. The existing Caddy must reach this
   watcher on its verified shared network.
8. Check `/health` for `guarded`, `NO_SEND` and runtime availability; verify actual
   source observation separately because liveness does not prove source freshness.
   Verify public HTTPS, security headers, both opportunity APIs, genuine IDs,
   uncertainty/empty/error handling, signup/account behavior, and zero physical
   transport invocations. No-send account-email responses only acknowledge a
   request; no token delivery is claimed.
9. Verify exclusion remains effective after process failure/reboot scenarios
   under the approved window. Hold on any custody, schema, source or sender
   ambiguity. Preserve the current database and journals; never restore an older
   database over newer consent or transport facts.
10. Only after separate pilot approval install the bounded `pilot` and `smtp`
    configuration. Account verification mail requires the explicit `ACCOUNT`
    class as well as approved destinations; it shares the same durable caps and
    transport gate. Validate provider outcomes, opt-out races, sender shutdown,
    no duplicate legacy delivery, and uncertain calls held without unsafe resend.

## Operator enrollment and administrator audience

The public operator review route defaults to denied. A student or administrator
cookie never grants operator authority. Use the offline maintenance command
under the approved stopped-runtime boundary, with the common exact database,
custody, keys, hash, approval and quiescence arguments:

```text
python -m app.database.production_maintenance review-account <common-arguments>
  --user-id <verified-account-id> --decision APPROVED --actor <operator>
  --review-evidence <trusted-evidence-reference> --institution VIT_PUNE
  --cohort 2028 --branch <exact-current-canonical-branch> --role SUBSCRIBER
```

Decisions also include REJECTED, SUSPENDED and REVOKED. Reviews revoke existing
sessions and do not grant consent or destination ownership. Profile changes
invalidate review. Revoked accounts have no automatic resurrection path.

Administrator email uses a separate encrypted recipient association:

```text
python -m app.database.production_maintenance register-admin <common-arguments>
  --actor <operator> --review-evidence <verified-inbox-ownership-reference>
  --admin-destination-file <owner-only-external-mailbox-file>
python -m app.database.production_maintenance revoke-admin <common-arguments>
  --actor <operator> --admin-recipient-key <recorded-opaque-identity>
```

Registration prints only the opaque identity, is idempotent for identical
ownership evidence, and never sends. A revoked recipient cannot be silently
re-registered. Physical delivery still requires a separate matching pilot
allowlist. Fictional helper recipients are not admitted to production delivery.

## Recovery, privacy and handover

Use only the forward-only procedures in `production_custody_recovery.md`.
Backups restore to a new inspection path; the independent current checkpoint is
never rolled back. Preserve UNKNOWN/ACK_FAILED and uncertain provider facts.
Privacy closure revokes access and consent; it does not falsely promise erasure.
Retain minimum delivery/consent evidence pending approved institutional retention
policy; protected erasure needs explicit operator scope and recovery review.
Measure full-database fingerprint latency on the actual volume before pilot.

The active runtime runs expired-credential pruning at most once daily, with a
30-day retention grace. This removes expired session/destination/action tokens;
it does not remove consent audits or delivery facts. The physical SMTP adapter
has one absolute operation deadline and rechecks pilot expiry before DATA. Its
dedicated thread cannot block the main event loop. A deadline or cancellation
holds the runtime; UNKNOWN is never treated as nonacceptance or physical END.
The process lock remains held until the physical thread ends. A late provider
result does not authorize an automatic resend; reconcile the durable invocation
under the separately approved recovery procedure. Account mutations serialize
with an in-progress physical send, so a message already admitted may still
arrive when an opt-out request is pending.

After all validation, prepare logical commits on the release branch. Preserve
PR #11 as the explicit reviewed prerequisite; propose its merge followed by a
release PR, or a reviewed stacked PR, according to the owner's publication
approval. No force push, history rewrite or silent absorption of PR #11.
Attach every created PR to this chat. Production verification and cleanup of
previous staging resources must have actual evidence before final completion.
