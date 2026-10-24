# Retention policy decision — for human review (Gate F)

Status: **DECISION REQUEST — no history is deleted or pruned by this document.**

## What the new durable stores hold

| Store | Fields | Private data? | Natural lifetime |
| --- | --- | --- | --- |
| `pending_notifications.event_snapshot_json` | Full opportunity record JSON (company, dates, packages, programmes) — identical category to `companies.raw_data_json` | No PII (no addresses, no tokens) | Row deleted when the admin obligation completes — snapshots are transient with their obligation |
| `notification_deliveries.raw_data_snapshot` | Same opportunity record per recipient row | No PII | Overwritten per distinct event; bounded by the (user, company, type) unique key |
| `notification_delivery_outcome_log` | delivery_id, **internal user_id (integer)**, company_id, type, prior_status, event snapshot copy, fixed-category outcome_note, timestamp | No direct identifiers (user_id is pseudonymous; no email) | **Append-only, currently unbounded** — grows only when a terminal FAILED event is later superseded by a distinct one |
| `system_state` ambiguity markers | `notification:v1:ambiguous_acceptance:<delivery_id>` = timestamp | No | **Currently never removed** — count exposed in `/health/details` |

## Bounded storage under an explicitly fictional workload

Assume 200 companies, ~5 meaningful changes each per year, 100 active
subscribers, 5% of UPDATE events exhausting retries: ~1,000 obligations/year
(transient, ~1.5 KB each while pending), ~50 outcome-log rows/year (~75 KB/year),
a handful of ambiguity markers/year. Delivery-row snapshots are bounded by the
unique key ceiling (~200×100×2 rows ≈ tens of MB worst case, reused). All
stores are inside the ordinary SQLite backup envelope and covered by the
existing backup path and privacy-logging rules.

## Decisions requested

1. Outcome-log retention (e.g., "keep 12 months of terminal-outcome history,
    then prune in bulk" — or keep indefinitely for audit).
2. Ambiguity-marker lifecycle (remove after operator review recorded, or
    keep as a permanent incident register).
3. Whether pending-obligation snapshots need any minimum retention after
    completion for dispute resolution (today they vanish with the row).

## Operator investigation without replay

Markers and outcome history are read-only evidence; investigating an
ambiguous acceptance never requires (and must never trigger) a resend:

```sql
-- Which deliveries had provider-accepted but unacknowledged sends?
SELECT key, value FROM system_state
 WHERE key LIKE 'notification:v1:ambiguous_acceptance:%' ORDER BY value;
-- Terminal history of a delivery (prior FAILED events and their snapshots):
SELECT prior_status, outcome_note, recorded_at
  FROM notification_delivery_outcome_log WHERE delivery_id = ?;
```

The operator decision for each marker is: accept the at-least-once duplicate
(if the retry also delivered) or treat the first acceptance as the delivery
(if the retry failed); either way record the disposition with the marker's
timestamp in the operations log.
