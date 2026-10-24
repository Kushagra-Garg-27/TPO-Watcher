"""F02: absence reconciliation requires explicit completeness authority.

Default V1 observations carry none, so a non-empty but partial feed must
preserve stored companies exactly like an empty feed. Positive updates remain
independent of the completeness decision.
"""
import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

import pytest
from app.monitoring.detector import CompanyDetector
from app.tpo.models import CompanyRecord


@pytest.fixture
def audit_repo(tmp_path):
    path = (tmp_path / "feed-completeness-gate.sqlite").resolve()
    assert path.is_relative_to(isolation.ROOT)
    from app.database.repository import DatabaseRepository
    return DatabaseRepository(str(path))


def rec(cid, name="Fictional Employer", **changes):
    values = dict(id=cid, company=name, isactive="True",
                  tpoprogram="VIT-BTech-Computer Engineering", organization="VIT")
    values.update(changes)
    return CompanyRecord(**values)


@pytest.fixture
def detector(audit_repo):
    audit_repo.set_baseline_initialized()
    return CompanyDetector(audit_repo)


def seed_three(repo):
    CompanyDetector(repo).process_fetched_companies([rec("A"), rec("B"), rec("C")])


def test_partial_feed_preserves_absent_companies(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies([rec("A")])
    assert result.deactivated_companies == []
    assert audit_repo.get_company("B")["is_active"] == "True"
    assert audit_repo.get_company("C")["is_active"] == "True"


def test_explicitly_complete_feed_deactivates_absent_companies(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies([rec("A")], observation_complete=True)
    assert result.deactivated_companies == ["B", "C"]
    assert audit_repo.get_company("B")["is_active"] == "False"
    assert audit_repo.get_company("C")["is_active"] == "False"


def test_empty_feed_preserves_active_state(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies([])
    assert result.deactivated_companies == []
    for cid in ("A", "B", "C"):
        assert audit_repo.get_company(cid)["is_active"] == "True"


def test_positive_updates_apply_without_completeness(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies(
        [rec("A", maxPackage="30000.0"), rec("B"), rec("C")])
    assert result.deactivated_companies == []
    assert len(result.updated_companies) == 1
    assert audit_repo.get_company("A")["max_package"] == "30000.0"


def test_reordered_records_are_equivalent(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies([rec("C"), rec("A"), rec("B")])
    assert result.deactivated_companies == []
    assert result.updated_companies == []
    for cid in ("A", "B", "C"):
        assert audit_repo.get_company(cid)["is_active"] == "True"


def test_reappearing_company_reactivates_via_positive_evidence(audit_repo, detector):
    seed_three(audit_repo)
    detector.process_fetched_companies([rec("A")], observation_complete=True)
    assert audit_repo.get_company("B")["is_active"] == "False"
    # A later observation containing B again reactivates it through the
    # normal upsert path, independent of completeness authority.
    detector.process_fetched_companies([rec("A"), rec("B")])
    assert audit_repo.get_company("B")["is_active"] == "True"


def test_unknown_explicit_activity_state_never_deactivates(audit_repo, detector):
    seed_three(audit_repo)
    result = detector.process_fetched_companies(
        [rec("A"), rec("B", isactive=None), rec("C")])
    assert result.deactivated_companies == []
    assert audit_repo.get_company("B")["is_active"] == "True"
    assert not any("Active Status" in c for _, cs in result.updated_companies for c in cs)
