"""F11: cross-release worker overlap is unsafe by construction.

Encodes why docs/deployment_worker_compatibility.md mandates single-owner
quiescence: the old generation's numeric-ID mutations cannot finalize or
disturb the new generation's token-bearing claims, and recovery must go
through lease expiry.
"""
import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

from app.database.sqlite_repository import SQLiteDeliveryRepository

from test_cummins_pipeline_stress import (
    audit_repo, delivery_row, expire_claim, queue_one,
)


def test_f11_old_generation_ack_and_release_cannot_finalize_new_claim(audit_repo):
    queue_one(audit_repo)
    new_claim = audit_repo.deliveries.claim_pending_batch(batch_size=1)[0]

    # An old-generation worker only has the numeric identity (e.g. after a
    # JSON serialization boundary). Neither mark nor release has any effect.
    plain = int(new_claim["id"])
    assert audit_repo.deliveries.mark_sent(plain) is False
    assert audit_repo.deliveries.release_failed(plain, "old worker drain") is False
    row = delivery_row(audit_repo)
    assert row["status"] == "PROCESSING" and row["attempt_count"] == 1

    # The row returns only through lease expiry, never through the old
    # generation's authority.
    expire_claim(audit_repo, new_claim["id"])
    assert audit_repo.deliveries.recover_stale_leases() == 1
    reclaimed = audit_repo.deliveries.claim_pending_batch(batch_size=1)[0]
    assert reclaimed["attempt_count"] == 2
    assert audit_repo.deliveries.mark_sent(reclaimed["id"]) is True
    assert delivery_row(audit_repo)["status"] == "SENT"


def test_f11_independent_repository_connection_cannot_steal_active_claim(audit_repo):
    queue_one(audit_repo)
    claim = audit_repo.deliveries.claim_pending_batch(batch_size=1)[0]
    other = SQLiteDeliveryRepository(audit_repo.db_path)
    # A concurrent claimer (any generation) sees no pending rows while the
    # lease is live.
    assert other.claim_pending_batch(batch_size=1) == []
    assert delivery_row(audit_repo)["attempt_count"] == 1
    assert audit_repo.deliveries.mark_sent(claim["id"]) is True
