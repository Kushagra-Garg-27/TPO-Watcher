import logging
import re
from enum import Enum
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)

class CanonicalBranch(str, Enum):
    # Current 12 B.Tech Programmes (VIT Pune Undergraduate)
    VIT_CE = "VIT_CE"
    VIT_CSE_DS = "VIT_CSE_DS"
    VIT_IT = "VIT_IT"
    VIT_CSE_IOT_CS_BC = "VIT_CSE_IOT_CS_BC"
    VIT_CSE_AI = "VIT_CSE_AI"
    VIT_CSE_AIML = "VIT_CSE_AIML"
    VIT_AIDS = "VIT_AIDS"
    VIT_CE_SE = "VIT_CE_SE"
    VIT_ENTC = "VIT_ENTC"
    VIT_ICE = "VIT_ICE"
    VIT_MECH = "VIT_MECH"
    VIT_CIVIL = "VIT_CIVIL"

    # Historical / Backward Compatibility (Preserved for existing data and TPO matching)
    VIT_CS_AI = "VIT_CS_AI"
    VIT_ELEC = "VIT_ELEC"

# Display names for UI/registration
CANONICAL_BRANCH_DISPLAY: Dict[CanonicalBranch, str] = {
    CanonicalBranch.VIT_CE: "B.Tech Computer Engineering",
    CanonicalBranch.VIT_CSE_DS: "B.Tech Computer Science and Engineering (Data Science)",
    CanonicalBranch.VIT_IT: "B.Tech Information Technology",
    CanonicalBranch.VIT_CSE_IOT_CS_BC: "B.Tech Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)",
    CanonicalBranch.VIT_CSE_AI: "B.Tech Computer Science and Engineering (Artificial Intelligence)",
    CanonicalBranch.VIT_CSE_AIML: "B.Tech Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    CanonicalBranch.VIT_AIDS: "B.Tech Artificial Intelligence and Data Science",
    CanonicalBranch.VIT_CE_SE: "B.Tech Computer Engineering (Software Engineering)",
    CanonicalBranch.VIT_ENTC: "B.Tech Electronics and Telecommunication Engineering",
    CanonicalBranch.VIT_ICE: "B.Tech Instrumentation and Control Engineering",
    CanonicalBranch.VIT_MECH: "B.Tech Mechanical Engineering",
    CanonicalBranch.VIT_CIVIL: "B.Tech Civil Engineering",
    # Historical
    CanonicalBranch.VIT_CS_AI: "B.Tech Computer Science and Artificial Intelligence",
    CanonicalBranch.VIT_ELEC: "B.Tech Electronics Engineering",
}

# Explicit mappings derived exclusively from observed TPO database values.
# Under NO circumstances should branch equivalence be assumed (e.g. CE is NOT CS, AI&DS is NOT AI&ML).
CANONICAL_TO_TPO_STRINGS: Dict[CanonicalBranch, List[str]] = {
    CanonicalBranch.VIT_CE: [
        "VIT-BTech-Computer Engineering",
        "BTech-Computer Engineering"
    ],
    CanonicalBranch.VIT_CSE_DS: [
        "VIT-BTech-Computer Science and Engineering (Data Science)",
        "BTech-Computer Science and Engineering (Data Science)",
        "VIT-BTech-Computer Science and Engineering(Data Science)",
        "BTech-Computer Science and Engineering(Data Science)",
        "VIT-BTech - Computer Science and Engineering (Data Science)",
        "BTech - Computer Science and Engineering (Data Science)",
    ],
    CanonicalBranch.VIT_IT: [
        "VIT-BTech-Information Technology",
        "BTech-Information Technology"
    ],
    CanonicalBranch.VIT_CSE_IOT_CS_BC: [
        "VIT-BTech-Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)",
        "BTech-Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)",
        "VIT-BTech - Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)",
        "BTech - Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)",
        "VIT-BTech-Computer Science and Engineering (IoT and Cyber Security Including Blockchain Technology)",
        "BTech-Computer Science and Engineering (IoT and Cyber Security Including Blockchain Technology)",
    ],
    CanonicalBranch.VIT_CSE_AI: [
        "VIT-BTech-Computer Science and Engineering (Artificial Intelligence)",
        "BTech-Computer Science and Engineering (Artificial Intelligence)"
    ],
    CanonicalBranch.VIT_CSE_AIML: [
        "VIT-BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
        "BTech - Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
        "VIT-BTech-Computer Science and Engineering (AI & Machine Learning)",
        "BTech-Computer Science and Engineering (AI & Machine Learning)"
    ],
    CanonicalBranch.VIT_AIDS: [
        "VIT-BTech-Artificial Intelligence & Data Science",
        "BTech-Artificial Intelligence & Data Science",
        "VIT-BTech-Artificial Intelligence and Data Science",
        "BTech-Artificial Intelligence and Data Science"
    ],
    CanonicalBranch.VIT_CE_SE: [
        "VIT-BTech-Computer Engineering (Software Engineering)",
        "BTech-Computer Engineering (Software Engineering)",
        "VIT-BTech-Computer Engineering(Software Engineering)",
        "BTech-Computer Engineering(Software Engineering)",
        "VIT-BTech - Computer Engineering (Software Engineering)",
        "BTech - Computer Engineering (Software Engineering)"
    ],
    CanonicalBranch.VIT_ENTC: [
        "VIT-BTech-Electronics and Telecommunication Engg",
        "BTech-Electronics and Telecommunication Engg",
        "VIT-BTech-Electronics and Telecommunication Engineering",
        "BTech-Electronics and Telecommunication Engineering"
    ],
    CanonicalBranch.VIT_ICE: [
        "VIT-BTech-Instrumentation and Control Engineering",
        "BTech-Instrumentation and Control Engineering",
        "VIT-BTech-Instrumentation & Control Engineering",
        "BTech-Instrumentation & Control Engineering",
        "VIT-BTech-Instrumentation and Control Engg",
        "BTech-Instrumentation and Control Engg",
        "VIT-BTech - Instrumentation and Control Engineering",
        "BTech - Instrumentation and Control Engineering"
    ],
    CanonicalBranch.VIT_MECH: [
        "VIT-BTech-Mechanical Engineering",
        "BTech-Mechanical Engineering"
    ],
    CanonicalBranch.VIT_CIVIL: [
        "VIT-BTech-Civil Engineering",
        "BTech-Civil Engineering",
        "VIT-BTech - Civil Engineering",
        "BTech - Civil Engineering"
    ],
    CanonicalBranch.VIT_CS_AI: [
        "VIT-B.Tech. Computer Science and Artificial Intelligence",
        "B.Tech. Computer Science and Artificial Intelligence"
    ],
    CanonicalBranch.VIT_ELEC: [
        "VIT-BTech-Electronics Engineering",
        "BTech-Electronics Engineering"
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

    # Normalize tokens from tpoprogram and programnew (split by commas and semicolons)
    tokens = set()
    for s in re.split(r"[,;]", tpoprogram_str or ""):
        st = s.strip()
        if st:
            tokens.add(st)
    for s in re.split(r"[,;]", programnew_str or ""):
        st = s.strip()
        if st:
            tokens.add(st)

    for branch, patterns in CANONICAL_TO_TPO_STRINGS.items():
        for pattern in patterns:
            pattern_lower = pattern.lower()
            # 1. Exact token match (primary and strict)
            if any(pattern_lower == token.lower() for token in tokens):
                eligible.add(branch)
                break
            # 2. Strict boundary match within combined text: ensure pattern is not followed by a specialization in parentheses
            esc = re.escape(pattern_lower)
            if re.search(r'(?<![a-zA-Z0-9])' + esc + r'(?!\s*[\(\/a-zA-Z0-9])', combined_text.lower()):
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
