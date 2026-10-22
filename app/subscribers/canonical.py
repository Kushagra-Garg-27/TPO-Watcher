import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Set, Optional

from app.tpo.normalization import normalize_token, normalize_organizations, tokenize_programmes

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

@dataclass(frozen=True)
class ProgrammeEvidence:
    branches: frozenset[CanonicalBranch]
    unresolved_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EligibilityEvidence:
    primary: ProgrammeEvidence
    secondary: ProgrammeEvidence
    organization_reason: Optional[str]

    @property
    def branches(self) -> frozenset[CanonicalBranch]:
        if self.organization_reason:
            return frozenset()
        return self.primary.branches | self.secondary.branches

    @property
    def fields_disagree(self) -> bool:
        return self.primary != self.secondary


def parse_programme_field(value: Optional[str]) -> ProgrammeEvidence:
    field = tokenize_programmes(value)
    if field.malformed:
        return ProgrammeEvidence(frozenset(), ("MALFORMED_SEGMENTATION",))
    # Generic catalogue entries are historical evidence, never attribution.
    qualified = {normalize_token(alias): branch
                 for branch, aliases in CANONICAL_TO_TPO_STRINGS.items()
                 for alias in aliases if alias.startswith("VIT-")}
    qualified.update({normalize_token(branch.value): branch for branch in CanonicalBranch})
    branches, reasons = set(), []
    for token in field.tokens:
        if token in qualified:
            branches.add(qualified[token])
        elif token.startswith(("viit-", "vu-", "unknown-")):
            reasons.append("FOREIGN_OR_UNKNOWN_INSTITUTE")
        elif token.startswith(("vit-", "vit_")):
            reasons.append("UNSUPPORTED_VIT_PROGRAMME")
        else:
            reasons.append("UNPROVEN_INSTITUTE_ATTRIBUTION")
    return ProgrammeEvidence(frozenset(branches), tuple(reasons))


def explain_eligible_canonical_branches(tpoprogram_str: Optional[str], programnew_str: Optional[str],
                                      organization_str: Optional[str] = None) -> EligibilityEvidence:
    organization = normalize_organizations(organization_str)
    reason = ("ORGANIZATION_UNRESOLVED" if organization.malformed or not organization.tokens
              else "ORGANIZATION_EXCLUDES_VIT" if "vit" not in organization.tokens else None)
    return EligibilityEvidence(parse_programme_field(tpoprogram_str),
                               parse_programme_field(programnew_str), reason)


def extract_eligible_canonical_branches(tpoprogram_str: Optional[str], programnew_str: Optional[str],
                                       organization_str: Optional[str] = None) -> Set[CanonicalBranch]:
    evidence = explain_eligible_canonical_branches(tpoprogram_str, programnew_str, organization_str)
    if evidence.primary.unresolved_reasons or evidence.secondary.unresolved_reasons or evidence.organization_reason:
        logger.debug("Programme attribution unresolved: organization=%s primary=%s secondary=%s",
                     evidence.organization_reason, evidence.primary.unresolved_reasons,
                     evidence.secondary.unresolved_reasons)
    return set(evidence.branches)
