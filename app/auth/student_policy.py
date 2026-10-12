"""Pure, unwired institutional identity policy for the Class of 2028 MVP.

This module proves no ownership/enrollment and accepts no signup payloads.
TrustedIdentityState represents future server-authoritative stored state, never
data deserialized from a client. Constructing it is not itself authentication.
No existing production route or subscriber operation imports this module.
"""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


POLICY_VERSION = "vit-pune-2028-provisional-v1"
_LOCAL_PART = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", re.ASCII)


class EligibilityOutcome(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    PROVISIONALLY_ELIGIBLE_2028 = "PROVISIONALLY_ELIGIBLE_2028"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class TrustedReviewDecision(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


@dataclass(frozen=True)
class TrustedIdentityState:
    """Future authoritative verification/review state; NOT a request model.

    Neither declared year nor branch is an input. Trusted exception approval
    and durable account state are deliberately left to later implementation.
    """

    institutional_verified: bool = False
    decision: TrustedReviewDecision = TrustedReviewDecision.UNREVIEWED

    def __post_init__(self):
        if type(self.institutional_verified) is not bool:
            raise TypeError("Institutional verification must be authoritative boolean state")
        if not isinstance(self.decision, TrustedReviewDecision):
            raise TypeError("Review state must be a TrustedReviewDecision")


def normalize_institutional_email(address: str) -> str:
    """Accept a conservative ASCII dot-atom local part at exactly vit.edu.

    Reject controls before trimming, including CR/LF at the edges. Domain case
    is folded; local-part case and dots are preserved. Plus/quoted/Unicode
    institutional aliases are unsupported until institute policy confirms them;
    no provider-specific aliases are invented or merged.
    """
    if not isinstance(address, str):
        raise TypeError("Institutional email must be text")
    if any(unicodedata.category(character).startswith("C") for character in address):
        raise ValueError("Institutional email contains a control character")
    normalized = address.strip()
    if len(normalized) > 254 or normalized.count("@") != 1:
        raise ValueError("Malformed institutional email")
    local, domain = normalized.split("@")
    if not domain.isascii() or domain.lower() != "vit.edu":
        raise ValueError("Exact institutional domain vit.edu required")
    if len(local) > 64 or not _LOCAL_PART.fullmatch(local):
        raise ValueError("Unsupported or malformed institutional local part")
    return local + "@vit.edu"


def has_terminal_24_indicator(address: str) -> bool:
    """A naming signal only: never evidence of ownership, branch, or enrollment."""
    return normalize_institutional_email(address).split("@", 1)[0].endswith("24")


def evaluate_eligibility(
    address: str,
    state: TrustedIdentityState,
    *,
    provisional_policy_enabled: bool = True,
) -> EligibilityOutcome:
    """Return policy outcome using explicit server-authoritative state.

    This pure API intentionally cannot accept signup dictionaries, year/branch,
    or trusted-exception approvals. An unsupported email raises rather than
    acquiring provisional access. Review outcomes grant no student authority.
    """
    if not isinstance(state, TrustedIdentityState):
        raise TypeError("Server-authoritative TrustedIdentityState required")
    if type(provisional_policy_enabled) is not bool:
        raise TypeError("Provisional policy switch must be authoritative boolean state")
    canonical = normalize_institutional_email(address)
    if state.decision is TrustedReviewDecision.SUSPENDED:
        return EligibilityOutcome.SUSPENDED
    if state.decision is TrustedReviewDecision.REJECTED:
        return EligibilityOutcome.REJECTED
    if not state.institutional_verified:
        return EligibilityOutcome.UNVERIFIED
    if provisional_policy_enabled and has_terminal_24_indicator(canonical):
        return EligibilityOutcome.PROVISIONALLY_ELIGIBLE_2028
    return EligibilityOutcome.REVIEW_REQUIRED
