# Temporary SSH ingress lifecycle checklist (F15)

Status: **CHECKLIST ONLY — no AWS change is authorized by this document.**

Historical fact (documented, not freshly observed): a workstation-specific
TCP/22 `/32` ingress rule was added to the production security group during
24 October 2026 diagnostics; five pre-existing rules were preserved. The
rule's exact removable identity is held in a private local DPAPI record and
must not be decrypted, published or modified by tooling.

## Required decisions (human owner)

| Item | Decision needed | Owner |
| --- | --- | --- |
| Responsible operator | Name of the human who owns this access path | TBD |
| Expiry decision | Explicit removal date, or "remove after alternate access verified" | TBD |
| Alternate access | A verified non-/32 access path (different network, SSM, or re-addable rule procedure) | TBD |

## Authorized removal procedure (future, separate approval)

1. Verify alternate access works end-to-end (SSH round trip) before touching
   the security group.
2. Decrypt the private identity record only within the approved operation to
   recover the exact rule ID/parameters.
3. Revoke exactly that rule; assert the other rules are unchanged by listing
   before/after and diffing.
4. Record the removal with timestamps and the diff in the operations log.

Never batch this removal with an application release. Never remove other
ingress rules. If the exact identity cannot be recovered, stop and escalate
rather than deleting by approximation.
