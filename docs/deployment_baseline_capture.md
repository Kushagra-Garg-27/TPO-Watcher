# Future deployment baseline capture procedure (F13)

Status: **PROCEDURE — `SCOPE_A_CONFIGURATION_UNVERIFIED` stands.**

The historical Scope A comparison could not fully pass because the previous
watcher container's complete environment and some HostConfig metadata were
never retained. This document defines the capture that must happen **before
every future release**, so the before/after comparison is complete and
secret-free by construction.

## Pre-release capture (mandatory, before stopping the old container)

For each container (watcher, caddy) record, sanitized:

1. **Image identity**: image ID digest + `org.opencontainers.image.revision`
   and `.created` labels.
2. **Environment semantics**: the complete set of environment KEY NAMES, and
   for each key a bounded per-key value fingerprint
   (`sha256(value)[:16]`) — never the value itself. Comparison is
   order-insensitive across the key set and flags added/removed/duplicated
   keys and changed fingerprints individually.
3. **Mounts**: destination, source (named volume vs bind), ro/rw mode, and
   propagation — as a set, order-insensitive, duplicates preserved as
   duplicates.
4. **HostConfig security semantics**: privileged, cap-add/cap-drop sets,
   read_only rootfs, no-new-privileges, port bindings (host IP included),
   restart policy, log driver/options.
5. **File checksums**: compose file, Caddyfile, and any bind-mounted config
   (sha256, not contents).

Store the capture beside the release receipt with a timestamp and the exact
pre-release SHA. Post-release, compare capture-vs-live with a script that
fails on ANY difference and never normalizes away duplicates, ordering-only
differences must be reported as "same semantics, different order" rather than
silently passed.

## Current-release exception record

Scope A (7b0797c) remains under the documented human risk acceptance: Caddy's
combined hash difference was proven ordering-only and current watcher
effective environment matched intended Compose/image defaults, but the
destroyed prior watcher environment cannot be reconstructed. No technical
PASS may be claimed retroactively.
