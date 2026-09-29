import logging
from typing import List, Dict, Any
from app.tpo.models import CompanyRecord
from app.database.interfaces import UserRepositoryProtocol, DeliveryRepositoryProtocol
from app.subscribers.canonical import extract_eligible_canonical_branches
from app.subscribers.matcher import SubscriptionMatcher

logger = logging.getLogger(__name__)

class FanoutEngine:
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
                        "notification_type": notification_type
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
