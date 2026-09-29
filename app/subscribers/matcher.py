import logging
from typing import Dict, Any, Set
from app.tpo.models import CompanyRecord
from app.subscribers.canonical import CanonicalBranch, extract_eligible_canonical_branches

logger = logging.getLogger(__name__)

class SubscriptionMatcher:
    @staticmethod
    def classify_opportunity_type(company: CompanyRecord) -> Dict[str, bool]:
        """
        Classifies an opportunity into internship, placement, and ppo dimensions
        based on observed TPO placementtype and internshiptype strings.
        """
        pt = (company.placementtype or "").lower()
        it = (company.internshiptype or "").lower()

        is_internship = "intern" in pt or ("intern" in it and "not mentioned" not in it)
        is_ppo = "ppo" in pt or "ppo" in it
        
        # Placement is full-time if placementtype specifies "placement"
        # or if it's not marked exclusively as an internship
        is_placement = False
        if "placement" in pt:
            is_placement = True
        elif not is_internship and not is_ppo:
            # Default to placement if not specifically designated as internship/PPO
            is_placement = True

        return {
            "internship": is_internship,
            "placement": is_placement,
            "ppo": is_ppo
        }

    @classmethod
    def matches_opportunity_preferences(
        cls, 
        company: CompanyRecord, 
        pref_internship: bool, 
        pref_placement: bool, 
        pref_ppo: bool
    ) -> bool:
        """
        Checks if at least one opportunity type of the company matches an enabled preference.
        """
        types = cls.classify_opportunity_type(company)
        
        if types["internship"] and pref_internship:
            return True
        if types["ppo"] and pref_ppo:
            return True
        if types["placement"] and pref_placement:
            return True
            
        return False

    @classmethod
    def matches_subscriber(
        cls, 
        company: CompanyRecord, 
        user_record: Dict[str, Any],
        eligible_branches: Set[CanonicalBranch]
    ) -> bool:
        """
        Full eligibility check for a subscriber:
        1. Student's branch must be in the company's eligible canonical branches.
        2. Opportunity type must match the student's notification preferences.
        """
        user_branch = user_record.get("branch_canonical")
        if not user_branch:
            return False

        try:
            branch_enum = CanonicalBranch(user_branch)
        except ValueError:
            logger.warning(f"User {user_record.get('id')} has unknown branch '{user_branch}'")
            return False

        if branch_enum not in eligible_branches:
            return False

        return cls.matches_opportunity_preferences(
            company=company,
            pref_internship=bool(user_record.get("pref_internship", 1)),
            pref_placement=bool(user_record.get("pref_placement", 1)),
            pref_ppo=bool(user_record.get("pref_ppo", 1))
        )
