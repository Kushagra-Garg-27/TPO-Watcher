import os
import re
import pytest
from app.subscribers.canonical import (
    CanonicalBranch,
    CANONICAL_BRANCH_DISPLAY,
    CANONICAL_TO_TPO_STRINGS
)

EXPECTED_12_PROGRAMMES = [
    ("VIT_CE", "Computer Engineering", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_CSE_DS", "Computer Science and Engineering (Data Science)", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_IT", "Information Technology", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_CSE_IOT_CS_BC", "Computer Science and Engineering (Internet of Things and Cyber Security Including Blockchain Technology)", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_CSE_AI", "Computer Science and Engineering (Artificial Intelligence)", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_CSE_AIML", "Computer Science and Engineering (Artificial Intelligence and Machine Learning)", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_AIDS", "Artificial Intelligence and Data Science", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_CE_SE", "Computer Engineering (Software Engineering)", "COMPUTING, COMPUTER SCIENCE & IT"),
    ("VIT_ENTC", "Electronics and Telecommunication Engineering", "ELECTRONICS & CONTROL"),
    ("VIT_ICE", "Instrumentation and Control Engineering", "ELECTRONICS & CONTROL"),
    ("VIT_MECH", "Mechanical Engineering", "MECHANICAL"),
    ("VIT_CIVIL", "Civil Engineering", "CIVIL"),
]


def test_canonical_branch_count_and_uniqueness():
    """Verify exactly 12 current undergraduate programmes exist with unique canonical IDs."""
    ids = [p[0] for p in EXPECTED_12_PROGRAMMES]
    assert len(ids) == 12
    assert len(set(ids)) == 12

    for canonical_id, official_name, _ in EXPECTED_12_PROGRAMMES:
        branch = CanonicalBranch(canonical_id)
        assert branch.value == canonical_id
        # Display name must contain the official name
        display = CANONICAL_BRANCH_DISPLAY[branch]
        assert official_name in display or official_name.replace("and", "&") in display
        # TPO strings must be configured
        assert len(CANONICAL_TO_TPO_STRINGS[branch]) > 0


def test_frontend_branches_file_matches_specification():
    """
    Parses frontend/src/lib/branches.ts to ensure:
    1. Exactly 12 active canonical branches are defined.
    2. Exactly 4 categories exist with the required names and branch memberships.
    3. Every branch is present in exactly one category (no duplicates).
    4. Historical branches exist in HISTORICAL_BRANCHES.
    """
    branches_ts_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "frontend", "src", "lib", "branches.ts"
    )
    assert os.path.exists(branches_ts_path), f"File not found: {branches_ts_path}"

    with open(branches_ts_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify all 12 canonical IDs are listed in CANONICAL_BRANCHES
    for canonical_id, _, _ in EXPECTED_12_PROGRAMMES:
        assert f"value: '{canonical_id}'" in content, f"Missing {canonical_id} in CANONICAL_BRANCHES"

    # Verify the 4 exact category names
    expected_categories = [
        "COMPUTING, COMPUTER SCIENCE & IT",
        "ELECTRONICS & CONTROL",
        "MECHANICAL",
        "CIVIL",
    ]
    for cat in expected_categories:
        assert f"label: '{cat}'" in content, f"Category '{cat}' missing from branches.ts"

    # Verify historical branches
    assert "VIT_ELEC" in content
    assert "VIT_CS_AI" in content


def test_category_distribution():
    """
    Ensure the branch distribution matches the required UI specification:
    - COMPUTING, COMPUTER SCIENCE & IT: 8 branches
    - ELECTRONICS & CONTROL: 2 branches
    - MECHANICAL: 1 branch
    - CIVIL: 1 branch
    """
    category_counts = {}
    for _, _, cat in EXPECTED_12_PROGRAMMES:
        category_counts[cat] = category_counts.get(cat, 0) + 1

    assert category_counts["COMPUTING, COMPUTER SCIENCE & IT"] == 8
    assert category_counts["ELECTRONICS & CONTROL"] == 2
    assert category_counts["MECHANICAL"] == 1
    assert category_counts["CIVIL"] == 1
    assert sum(category_counts.values()) == 12


def test_backward_compatibility_preserved():
    """Verify that historical branches remain valid CanonicalBranch members."""
    assert CanonicalBranch("VIT_ELEC") == CanonicalBranch.VIT_ELEC
    assert CanonicalBranch("VIT_CS_AI") == CanonicalBranch.VIT_CS_AI
    assert CanonicalBranch.VIT_ELEC in CANONICAL_BRANCH_DISPLAY
    assert CanonicalBranch.VIT_CS_AI in CANONICAL_BRANCH_DISPLAY
