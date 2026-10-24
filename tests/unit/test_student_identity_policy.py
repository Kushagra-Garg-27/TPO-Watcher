"""Fictional inputs test policy behavior, not the running subscription portal."""
import importlib.util
import pytest


def policy():
    assert importlib.util.find_spec("app.auth.student_policy") is not None, "Pure student policy module is missing"
    from app.auth import student_policy
    return student_policy


def test_standard_verified_suffix_is_provisional():
    p = policy()
    identity = p.normalize_institutional_email("fiction.student24@vit.edu")
    state = p.TrustedIdentityState(institutional_verified=True)
    assert p.evaluate_eligibility(identity, state) is p.EligibilityOutcome.PROVISIONALLY_ELIGIBLE_2028


def test_suffix_without_ownership_is_unverified():
    p = policy()
    assert p.evaluate_eligibility("fiction.student24@vit.edu", p.TrustedIdentityState()) is p.EligibilityOutcome.UNVERIFIED


@pytest.mark.parametrize("address", ["fiction.student24@VIT.EDU", "  fiction.student24@vit.edu  "])
def test_exact_domain_and_whitespace_normalize(address):
    assert policy().normalize_institutional_email(address) == "fiction.student24@vit.edu"


@pytest.mark.parametrize("address", [
    "fiction.student24@gmail.com", "fiction.student24@outlook.com",
    "fiction.student24@vit.edu.evil.com", "fiction.student24@sub.vit.edu",
    "fiction.student24@vіt.edu", "fiction.student24@vit.ｅdu",
    "fiction.student24@vit.edu@evil.com", "@vit.edu", "fiction@",
    ".fiction24@vit.edu", "fiction..student24@vit.edu", "fiction24.@vit.edu",
    "fiction student24@vit.edu", "Name <fiction24@vit.edu>",
    "fiction24\n@vit.edu", "fiction24@vit.edu\r\n", "fiction\x0024@vit.edu",
    "fiction\t24@vit.edu", "fiction24@vit.edu.", "fiction24+alias@vit.edu",
    "fiction+alias24@vit.edu", '"fiction24"@vit.edu', "fíction24@vit.edu",
    "fiction24@@vit.edu", "", "fiction24", "fiction/24@vit.edu",
])
def test_invalid_or_unsupported_identity_is_rejected(address):
    with pytest.raises(ValueError):
        policy().normalize_institutional_email(address)


@pytest.mark.parametrize("address", ["fiction24.student@vit.edu", "fiction.student23@vit.edu", "fiction.student25@vit.edu"])
def test_verified_nonmatching_suffix_requires_review(address):
    p = policy()
    assert p.evaluate_eligibility(address, p.TrustedIdentityState(institutional_verified=True)) is p.EligibilityOutcome.REVIEW_REQUIRED


@pytest.mark.parametrize("decision, outcome", [("REJECTED", "REJECTED"), ("SUSPENDED", "SUSPENDED")])
def test_trusted_adverse_decision_overrides_suffix(decision, outcome):
    p = policy()
    state = p.TrustedIdentityState(institutional_verified=True, decision=p.TrustedReviewDecision[decision])
    assert p.evaluate_eligibility("fiction.student24@vit.edu", state) is p.EligibilityOutcome[outcome]


def test_unapproved_provisional_policy_requires_review():
    p = policy()
    assert p.evaluate_eligibility("fiction.student24@vit.edu", p.TrustedIdentityState(institutional_verified=True), provisional_policy_enabled=False) is p.EligibilityOutcome.REVIEW_REQUIRED


@pytest.mark.parametrize("payload", [{"graduation_year": 2028}, {"branch_canonical": "VIT_CE"}, {"institutional_verified": True}])
def test_client_payload_is_not_trusted_state(payload):
    with pytest.raises(TypeError):
        policy().evaluate_eligibility("fiction.student24@vit.edu", payload)


def test_signup_fields_cannot_be_passed_as_policy_authority():
    p = policy()
    with pytest.raises(TypeError):
        p.evaluate_eligibility("fiction.student24@vit.edu", p.TrustedIdentityState(), graduation_year=2028)
    with pytest.raises(TypeError):
        p.TrustedIdentityState(branch_canonical="VIT_CE")


def test_canonicalization_preserves_dot_distinctions():
    p = policy()
    assert p.normalize_institutional_email("fiction.student24@vit.edu") != p.normalize_institutional_email("fictionstudent24@vit.edu")


def test_policy_version_is_explicit():
    assert policy().POLICY_VERSION == "vit-pune-2028-provisional-v1"
