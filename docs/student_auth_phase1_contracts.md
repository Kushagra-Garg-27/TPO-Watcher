# Student authentication Phase 1 contracts

These are the owner-approved future product rules. Current production remains
the existing verified-email subscription service. Phase 1 changes no route,
schema, frontend, subscriber selection, delivery behavior, or deployment.

## Implemented pure policy

`app/auth/student_policy.py` is unwired, pure, and has no configuration, database,
network, SMTP, or startup imports. Policy version:
`vit-pune-2028-provisional-v1`.

- Identity domain must be exactly ASCII `vit.edu`, case-insensitively.
- Trim surrounding whitespace; reject control characters before trimming.
- Require one `@`, a nonempty conservative ASCII dot-atom local part, local
  length at most 64 and total length at most 254. Reject display names, quoted
  local parts, malformed dots, lookalike domains, and subdomains.
- Preserve local-part case, dots, underscores, and hyphens. Domain is lowercase.
  Do not collapse Gmail-style dots or plus tags. Plus and Unicode institutional
  aliases are unsupported until an institute-approved alias policy exists.
  Unsupported forms raise an error and never acquire provisional authority;
  legitimate ambiguous aliases need trusted handling in a later review flow.
- No authoritative email naming specification was supplied. Terminal `24` in
  the local part is only the approved provisional admission-year indicator.
  It proves no mailbox ownership, enrollment, branch, or graduation status.

`TrustedIdentityState` is a frozen typed contract for future server-authoritative
verification/review state. It is not a client payload model or authentication
mechanism. A caller capable of constructing Python state is not thereby trusted;
later backend code must load authority from verified stored state. Dictionaries,
year/branch kwargs, nonboolean verification, and untyped review strings are not
accepted. Current signup payloads are not connected to the module.

| Authoritative state | Outcome |
|---|---|
| Explicit suspension | `SUSPENDED` |
| Explicit rejection | `REJECTED` |
| Ownership not verified | `UNVERIFIED` |
| Verified exact-domain identity, terminal `24`, provisional mode enabled | `PROVISIONALLY_ELIGIBLE_2028` |
| Other verified identity, or provisional mode disabled | `REVIEW_REQUIRED` |

Adverse decisions override the suffix and missing verification. Review grants no
student access. A trusted exception approval workflow is future work; this module
does not enroll a student or implement approval, login, or persistence.

Local-part case preservation intentionally avoids an undocumented alias rule.
Existing subscribers currently use lowercase email comparisons. Later migration
must reconcile that difference using an explicit institution-approved comparison
policy; it must not silently create duplicate identities or merge accounts.

## Future authentication and authorization requirements

- Only verified institutional identities can establish student login authority.
  A future STUDENT session also requires an allowed authoritative eligibility
  decision. Rejected/suspended identities cannot get student access.
- STUDENT, ONBOARDING, and LEGACY_MIGRATION scopes are distinct. An existing
  MANAGE_PREFS token or legacy personal-mailbox session cannot be upgraded to
  STUDENT authority. Durable revocation/logout are required in later phases.
- Personal notification emails grant neither student login nor account recovery.
  They require independent ownership verification and must never receive student
  login tokens. Changing a destination cannot automatically enable alerts.
- Preserve stable subscriber IDs, preferences, delivery history, valid unsubscribe
  capabilities, and opt-out consent. Never auto-merge accounts or promote personal
  mailbox verification into institutional verification.
- The future opportunity API requires backend authorization. Hiding React data
  is insufficient. Public static assets must contain no actual private placement
  examples after cutover. Already published copies cannot be recalled by login.

## Tests-only isolation

Preferred commands (use the project's Python environment):

```text
python -I -B tests/isolation_bootstrap.py --self-test
python -I -B tests/isolation_bootstrap.py --pytest -q tests/unit/test_student_identity_policy.py tests/integration/test_test_isolation.py
python -I -B tests/isolation_bootstrap.py --pytest -q
```

The helper is necessary because ordinary fixtures run after test-module imports.
It creates its own disposable root, installs process-wide audit/path/network/SMTP
guards, removes inherited application settings without printing/using their
values, supplies synthetic DB/state/key settings, disables dotenv settings-file
reads, and blocks real HTTP transports before application collection. The runner
loads pytest only after these controls and disables third-party plugin autoload;
pytest-asyncio and pytest-mock are explicitly enabled. No dependencies are added.

The first lines of `tests/conftest.py` also install the guards for plain pytest,
before test modules are imported. If relevant application modules are already
loaded, installation refuses to claim isolation. Plain pytest may load external
plugins *before* conftest, so it is not equivalent to the early runner against an
unknown plugin set; use the documented runner for reliable startup ordering.

File-backed SQLite paths must be absolute and belong to the generated disposable
root, including import-time constants and captured defaults. Relative/default
filenames are denied; in-memory databases are permitted. File mutations outside it and sensitive-file
reads are denied. SQLite ATTACH is checked; extensions are disabled. Per-test
working directories are disposable. Temporary backups and pytest output stay
there too. No persistent application DB may be used even in read-only mode.

Unmocked HTTP/DNS/socket traffic, SMTP/SMTP_SSL, and subprocess/browser/CLI launch
are denied before execution. Explicit fake email/HTTP transports and in-process
FastAPI TestClient remain usable. Windows asyncio's internal socket pair is
restricted to its freshly created loopback endpoint. The existing localhost
header regression alone may use ports freshly bound during that test; arbitrary
localhost services remain denied. The original SMTP guard regression is allowed
to assert expected denied SMTP calls, never to connect. Other swallowed guard
exceptions still fail during teardown; no guard is waived to make application
transports run.

## Guard limitations and current validation boundary

- This Python harness prevents accidental side effects, not malicious code.
  Trusted tests can replace wrappers, disable a SQLite authorizer, use native
  libraries/raw OS APIs, or pass previously opened descriptors. Such behavior is
  out of scope and must never be used to circumvent guards.
- Sensitive filenames and SQLite connection paths are intercepted; this is not
  an exhaustive secret-file classifier or protection from data read before
  startup. The runner does not inspect real credentials or private session data.
- Audit hooks cannot be removed; use a fresh process for every test run. Disposable
  roots are retained for inspection rather than cleaning broad system temp paths.
- External HTTP mocks intentionally bypass transport blocking. They must stay
  synthetic. No browser binaries are launched; watcher startup is never needed.
- Linux descriptor-based deletion resolves `/proc/self/fd` and requires the
  directory and target inside the root. Unsupported platforms fail closed. Linux
  runtime behavior still needs a later CI check; only Windows was run locally.
- A guard detecting an unsafe existing test is a validation blocker, not permission
  to whitelist it. The full-suite run exposed an unmocked Playwright retry in
  `test_reconciliation_test_d_fetch_failure_preserves_active_state`; correcting
  that existing test is outside this phase's approved file scope and needs owner
  review. No passing full-suite claim is made.

Future routes, sessions, eligibility review UI, contact verification, schema and
migrations, delivery changes, frontend gating, CI changes, publication, and
deployment require separate owner authorization.
