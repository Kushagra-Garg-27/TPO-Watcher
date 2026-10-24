# Recovery drill prerequisites (F14)

Status: **PRE-REQUISITES — no S3 GetObject/download is authorized here.**

## What exists (verified)

`scripts/recovery_verification.py` (recovery-readiness branch 9d26d82, tested
offline at 7c32fae) prepares an exact-object drill that:

- Requires POSIX and rejects live-database locations by path.
- Uses an explicitly named AWS profile only (no inherited/default/EC2 metadata
  credentials) and verifies the recovery identity before downloading.
- Pins the trusted receipt by SHA-256 (`receipt_sha256`
  `b1235518dd0d5eddf468c448deee6cce95ec59d83acfddf4f98c656a9d0b423f`) and the
  pinned object `watcher_20261024T122011Z.sqlite` (sha256
  `d7d746b322a2475700d26074db9b9022221ec326b40007085ad1c819ff5397c6`) with
  exact bucket/key/region/size fields.
- Downloads only into a private (0700/0600) isolated recovery directory.
- Validates with `mode=ro`, `PRAGMA query_only=ON`, `trusted_schema=OFF`,
  `integrity_check`, schema and bounded aggregate checks.
- Verifies cleanup and reports `recovery_cleanup_failed` rather than
  silently leaving copies.
- Treats S3 listing/ETag as non-proof; only a GetObject round trip with a
  matching SHA-256 counts.

## Remaining prerequisites for the real drill (each needs explicit approval)

1. A named recovery IAM identity with GetObject scoped to the exact object
   prefix — created and reviewed separately; the EC2 writer role must remain
   upload/list-only.
2. An approved Linux host with private encrypted storage for the recovered
   copy (never the production host's live volume path).
3. Human confirmation of the pinned receipt/object (above) as the drill
   target.
4. A cleanup plan for the recovered copy after verification.
5. Timeboxed execution window with no concurrent release activity.

Completion criteria: downloaded-object SHA-256 equals the pinned digest;
integrity/schema/aggregate checks pass read-only; cleanup verified. Until
then, restore capability remains **UNPROVEN** and RPO/RTO remain undefined
(no invented guarantees).
