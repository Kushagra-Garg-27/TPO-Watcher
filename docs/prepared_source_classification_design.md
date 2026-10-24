# Prepared design: source error classification and status typing (IDE 1 handoff)

Status: **PREPARED PATCH DESIGN — not applied.** `app/tpo/client.py` and
`tests/unit/test_tpo_response_diagnostics.py` are owned by the parallel IDE 1
session; nothing here modifies them. This document is the integration-ready
handoff once IDE 1 lands its status-marker diagnostic.

## Finding 1 — application error misclassified as authentication expiry (§27)

`client.py` (Scope A bytes): an object payload with a truthy `error` and NO
`company_list` raises `AuthenticationError("API authentication error:
application error payload")`. The watcher's AuthenticationError path then
deletes `playwright_state.json` and performs a full credential re-login. A
generic upstream application error therefore causes unnecessary session
destruction and burns the two-attempt budget with the wrong failure class.

### Proposed patch (client.py, ~4 lines)

Replace that branch with a distinct exception path:

```python
if isinstance(data, dict) and data.get("error") and not data.get("company_list"):
    _log_rejected_response(response, "application_error", data)
    raise SourceUpstreamError("upstream application error payload")
```

with `class SourceUpstreamError(Exception)` defined in client.py. The watcher
(`check_once`) already treats non-AuthenticationError exceptions as ordinary
failures (heartbeat failure, no session deletion) — no watcher change needed.
Diagnostic category remains `application_error`, so log consumers are
unaffected.

### Tests to add (IDE 1's diagnostic test file)

- Object `{error: {...}, }` without list → `SourceUpstreamError`, category
  `application_error`, and the auth state file is NOT deleted by the watcher
  recovery path.
- `error` with a present `company_list` still follows existing validation
  (unchanged).

## Finding 2 — status membership vs Pydantic typing (§28)

`validate_listing_body` accepts `status in (None, "success", "Success", True,
200)` (Python equality: `1` also passes), but `APIResponse.status:
Optional[str]` then rejects non-string statuses as `invalid_company_schema`.
A numeric truthy status therefore reports the wrong category
(`invalid_company_schema` instead of `invalid_status`).

### Proposed patch (models.py — integration-sensitive, coordinate with IDE 1)

Widen `APIResponse.status` to `Optional[Union[str, int, bool]]` (validation
semantics unchanged elsewhere), or narrow the validator tuple to
`(None, "success", "Success")` plus an explicit `status in (True, 200)` type
check. Either keeps acceptance identical while making the diagnostic category
truthful. Decide jointly with IDE 1's status-marker work; the midnight
evidence (`status` was a string, category `invalid_status`) makes the string
path the production-relevant one.

## Do-not list

- Do not auto-trim/normalize source status values.
- Do not accept arbitrary status strings to make the current response pass.
- Do not build a speculative envelope adapter while the status string's
  meaning is unknown.
