import logging
from typing import List, Dict, Any
from app.tpo.models import CompanyRecord
from app.database.interfaces import UserRepositoryProtocol, DeliveryRepositoryProtocol
from app.subscribers.canonical import extract_eligible_canonical_branches
from app.subscribers.matcher import SubscriptionMatcher

logger = logging.getLogger(__name__)

class FanoutEngine:
    @staticmethod
    def advance_v2(repository,handles,*,pages=20,faults=None):
        """Bounded durable fanout using the existing v2 writer transaction."""
        if type(pages) is not int or not 1<=pages<=20:
            raise ValueError("bounded fanout quantum required")
        from app.database.event_repository import Fenced, PolicyDenied
        faults=faults or {}
        progress=0
        attempted=set()
        for _ in range(pages):
            candidates=repository.c.execute("""SELECT * FROM event_outbox
                WHERE status IN('PENDING','PROCESSING') ORDER BY created_ms,intent_id,channel""").fetchall()
            selected=None
            for candidate in candidates:
                key=(candidate["intent_id"],candidate["channel"])
                if key in attempted:continue
                handle=handles.get(key)
                if candidate["status"]=="PROCESSING" and candidate["lease_ms"]>repository.now() and handle is None:
                    continue
                selected,key=candidate,key
                break
            if selected is None:break
            try:
                handle=handles.get(key)
                if handle is None:
                    handle=repository.claim_outbox(*key)
                    if handle is None:
                        attempted.add(key)
                        continue
                    handles[key]=handle
                if selected["sealed_ms"] is None:
                    repository.seal(handle,fault=faults.get("seal"))
                # Zero-recipient completion is explicit and has no delivery page.
                status=repository.c.execute("SELECT status FROM event_outbox WHERE intent_id=? AND channel=?",key).fetchone()[0]
                if status!="COMPLETE":
                    repository.fanout_page(handle,fault=faults.get("page"))
                progress+=1
                if repository.c.execute("SELECT status FROM event_outbox WHERE intent_id=? AND channel=?",key).fetchone()[0]=="COMPLETE":
                    handles.pop(key,None)
                    attempted.add(key)
            except (Fenced,PolicyDenied):
                handles.pop(key,None)
                attempted.add(key)
            except Exception:
                # The failed unit rolled back; keep its durable preparation for
                # fenced lease recovery, and give the other channel a turn.
                handles.pop(key,None)
                attempted.add(key)
        return progress

    def __init__(
        self, 
        user_repo: UserRepositoryProtocol, 
        delivery_repo: DeliveryRepositoryProtocol
    ):
        self.user_repo = user_repo
        self.delivery_repo = delivery_repo

    def dispatch_opportunity(
        self, 
        company: CompanyRecord, 
        notification_type: str = "NEW"
    ) -> int:
        """
        Fans out a detected opportunity to eligible, active, verified 2028 subscribers.
        Enforces database idempotency via delivery_repo.
        Returns the number of new deliveries enqueued.
        """
        eligible_branches = extract_eligible_canonical_branches(
            tpoprogram_str=company.tpoprogram,
            programnew_str=company.programnew,
            organization_str=company.organization
        )

        if not eligible_branches:
            logger.info(
                f"No eligible VIT branches extracted for company '{company.company}' (ID: {company.id}). "
                f"Skipping subscriber fan-out."
            )
            return 0

        branch_names = [b.value for b in eligible_branches]
        logger.info(
            f"Fan-out evaluating company '{company.company}' (ID: {company.id}) "
            f"for eligible branches: {branch_names}"
        )

        deliveries: List[Dict[str, Any]] = []
        seen_user_ids = set()

        for branch in eligible_branches:
            subscribers = self.user_repo.get_active_subscribers_for_branch(
                branch_canonical=branch.value,
                graduation_year=2028
            )
            for sub in subscribers:
                uid = sub["id"]
                if uid in seen_user_ids:
                    continue

                if SubscriptionMatcher.matches_subscriber(company, sub, eligible_branches):
                    deliveries.append({
                        "user_id": uid,
                        "company_id": str(company.id),
                        "notification_type": notification_type,
                        # F11: snapshot raw company state at dispatch time so the
                        # worker renders the deadline/package from the moment of
                        # dispatch, not the potentially-updated current DB row.
                        "raw_data_snapshot": company.model_dump_json(),
                    })
                    seen_user_ids.add(uid)

        if not deliveries:
            logger.info(f"No matching 2028 subscribers found for company '{company.company}'.")
            return 0

        enqueued_count = self.delivery_repo.enqueue_deliveries(deliveries)
        logger.info(
            f"Fan-out queued {enqueued_count} deliveries for company '{company.company}' "
            f"across {len(deliveries)} eligible candidate subscribers."
        )
        return enqueued_count
