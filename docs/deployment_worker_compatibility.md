# Cross-release worker compatibility and rollback protocol (F11)

Status: **PROTOCOL — no production restart is authorized by this document.**

The notification candidate (4208e31 lineage) introduces token-bearing claim
identities (`_ClaimedDeliveryId`): `mark_sent`, `release_failed` and
`current_delivery_context` accept authority only from the claim's durable
ownership token in `system_state`. The previously deployed worker acknowledges
by plain numeric ID and ignores token authority. The two generations must
never process deliveries concurrently.

## Established incompatibilities (code-verified, fictionally tested)

- `tests/integration/test_notification_v1_reliability.py::test_serialized_numeric_id_is_not_claim_authority`
  — a claim serialized to a plain int (as an old worker would pass after a
  JSON boundary) cannot mark or release a row.
- `tests/integration/test_notification_v1_reliability.py::test_old_claim_cannot_mutate_a_new_processing_claim`
  — a stale lease-holder cannot overwrite a newer claim's outcome.
- An old worker paused exactly at its send boundary cannot know the new
  generation's fencing state; its later numeric ack fails silently while the
  message may already be provider-accepted. That ambiguity must be resolved by
  an operator (see the ambiguity markers below), never by automatic replay.

## Required single-owner release sequence (future authorization)

1. **Freeze**: stop the scheduled source window; no release within 30 minutes
   of a configured check time (00:00 / 10:00 / 17:00 IST).
2. **Quiesce the old worker**: `docker stop tpo-watcher` (grace period ≥ 60s).
   Do not drain with the old image.
3. **Record pre-release state**: delivery status counts, `PROCESSING` rows and
   lease expiry timestamps, admin pending count, ambiguous-acceptance markers,
   plus a fresh transactional backup receipt and protected host copy.
4. **Verify rollback image present**: the immediately preceding application
   image, tagged with its exact revision, exists locally and its revision
   label matches the deployed SHA being replaced. Never target an older
   rollback image than the previous release.
5. **Start the new image** against the existing volume and configuration.
   First drain tick observes existing `PROCESSING` rows only after lease
   expiry; it recovers them per the approved at-least-once contract.
6. **Post-release verification**: revision label, `/health` and
   `/health/details` (watcher freshness, backlog, terminal failures), no
   unexplained SENT deltas, no automatic historical replay.

## Rollback (critical regression only, separate authorization)

Roll back only to the immediately preceding image, keeping the live volume.
Before rollback: stop the new worker completely (same quiesce), snapshot
post-release delivery counts, and note that rows claimed under token
authority recover through lease expiry under the old worker — the old
worker's numeric acks then apply again. Treat any provider-accepted-but-
unacknowledged sends from either generation as ambiguous: investigate, do not
resend blindly. Never reset the queue, never replay SENT history, never
re-baseline.

## Persistent-volume/schema compatibility

The candidate's schema changes are additive (`pending_notifications.event_snapshot_json`,
`notification_delivery_outcome_log`) and applied by idempotent guarded ALTER /
CREATE IF NOT EXISTS at startup. Old workers ignore them. A rollback leaves
the columns/tables in place unused — no downgrade migration is required or
provided; do not attempt to drop them.
