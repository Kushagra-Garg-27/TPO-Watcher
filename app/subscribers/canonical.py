import logging
from enum import Enum
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)

class CanonicalBranch(str, Enum):
    VIT_CE = "VIT_CE"
    VIT_IT = "VIT_IT"
    VIT_AIDS = "VIT_AIDS"
    VIT_CSE_AI = "VIT_CSE_AI"
    VIT_CSE_AIML = "VIT_CSE_AIML"
    VIT_CS_AI = "VIT_CS_AI"
    VIT_ENTC = "VIT_ENTC"
    VIT_ELEC = "VIT_ELEC"
    VIT_MECH = "VIT_MECH"

# Display names for UI/registration
CANONICAL_BRANCH_DISPLAY: Dict[CanonicalBranch, str] = {
    CanonicalBranch.VIT_CE: "B.Tech Computer Engineering",
    CanonicalBranch.VIT_IT: "B.Tech Information Technology",
    CanonicalBranch.VIT_AIDS: "B.Tech Artificial Intelligence & Data Science",
    CanonicalBranch.VIT_CSE_AI: "B.Tech Computer Science and Engineering (Artificial Intelligence)",
    CanonicalBranch.VIT_CSE_AIML: "B.Tech Computer Science and Engineering (AI & Machine Learning)",
    CanonicalBranch.VIT_CS_AI: "B.Tech Computer Science and Artificial Intelligence",
    CanonicalBranch.VIT_ENTC: "B.Tech Electronics and Telecommunication Engineering",
    CanonicalBranch.VIT_ELEC: "B.Tech Electronics Engineering",
    CanonicalBranch.VIT_MECH: "B.Tech Mechanical Engineering",
}

# Explicit mappings derived exclusively from observed TPO database values.
# Under NO circumstances should branch equivalence be assumed (e.g. CE is NOT CS, AI&DS is NOT AI&ML).
CANONICAL_TO_TPO_STRINGS: Dict[CanonicalBranch, List[str]] = {
    CanonicalBranch.VIT_CE: [
        "VIT-BTech-Computer Engineering",
        "BTech-Computer Engineering"
    ],
    CanonicalBranch.VIT_IT: [
        "VIT-BTech-Information Technology",
        "BTech-Information Technology"
    ],
    CanonicalBranch.VIT_AIDS: [
        "VIT-BTech-Artificial Intelligence & Data Science",
        "BTech-Artificial Intelligence & Data Science"
    ],
    CanonicalBranch.VIT_CSE_AI: [
        "VIT-BTech-Computer Science and Engineering (Artificial Intelligence)",
        "BTech-Computer Science and Engineering (Artificial Intelligence)"
    ],
    CanonicalBranch.VIT_CSE_AIML: [
        "VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
        "BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning)"
    ],
    CanonicalBranch.VIT_CS_AI: [
        "VIT-B.Tech. Computer Science and Artificial Intelligence",
        "B.Tech. Computer Science and Artificial Intelligence"
    ],
    CanonicalBranch.VIT_ENTC: [
        "VIT-BTech-Electronics and Telecommunication Engg",
        "BTech-Electronics and Telecommunication Engg"
    ],
    CanonicalBranch.VIT_ELEC: [
        "VIT-BTech-Electronics Engineering",
        "BTech-Electronics Engineering"
    ],
    CanonicalBranch.VIT_MECH: [
        "VIT-BTech-Mechanical Engineering",
        "BTech-Mechanical Engineering"
    ]
}

def extract_eligible_canonical_branches(tpoprogram_str: Optional[str], programnew_str: Optional[str], organization_str: Optional[str] = None) -> Set[CanonicalBranch]:
    """
    Extracts canonical VIT branches eligible for an opportunity.
    Enforces strict string matching based on observed values.
    Logs unknown/unmapped programs for administrative review rather than silently guessing.
    """
    eligible: Set[CanonicalBranch] = set()
    
    # Check organization: VIT students are eligible only if "VIT" is in the organizations list
    if organization_str:
        orgs = [o.strip().upper() for o in organization_str.split(",") if o.strip()]
        if "VIT" not in orgs:
            logger.info(f"Opportunity organization '{organization_str}' does not include VIT Pune. Skipping VIT branches.")
            return eligible

    combined_text = ""
    if tpoprogram_str:
        combined_text += " " + tpoprogram_str
    if programnew_str:
        combined_text += " " + programnew_str

    if not combined_text.strip():
        return eligible

    # Normalize tokens from tpoprogram and programnew
    tokens = set()
    for s in (tpoprogram_str or "").split(","):
        st = s.strip()
        if st:
            tokens.add(st)
    for s in (programnew_str or "").split(","):
        st = s.strip()
        if st:
            tokens.add(st)

    for branch, patterns in CANONICAL_TO_TPO_STRINGS.items():
        for pattern in patterns:
            # Check exact token membership or exact match within token list
            if any(pattern.lower() == token.lower() for token in tokens) or (pattern.lower() in combined_text.lower()):
                eligible.add(branch)
                break

    # Log unknown VIT programs for audit and future additions
    for token in tokens:
        if token.startswith("VIT-") and token != "VIT-null":
            is_recognized = False
            for patterns in CANONICAL_TO_TPO_STRINGS.values():
                if any(token.lower() == p.lower() for p in patterns):
                    is_recognized = True
                    break
            if not is_recognized:
                logger.warning(f"Unrecognized VIT program observed in TPO payload: '{token}'. Requires review.")

    return eligible
