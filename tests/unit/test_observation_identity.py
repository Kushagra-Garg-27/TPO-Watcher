"""F09: whole-observation identity/name validation before business writes.

The default V1 path must reject conflicting duplicate identities and blank
company names before any persistence; identical duplicates deduplicate.
"""
import isolation_bootstrap as isolation

if isolation.ROOT is None:
    raise RuntimeError("requires the established isolation bootstrap")

import pytest
from app.monitoring.detector import CompanyDetector
from app.tpo.listing import SourceEvidenceError
from app.tpo.models import CompanyRecord

SYNTHETIC_ID = "SYNTHETIC-OBSERVATION-001"
CE = "VIT-BTech-Computer Engineering"


@pytest.fixture
def audit_repo(tmp_path):
    path = (tmp_path / "observation-identity.sqlite").resolve()
    assert path.is_relative_to(isolation.ROOT)
    from app.database.repository import DatabaseRepository
    return DatabaseRepository(str(path))


def rec(**changes):
    values = dict(id=SYNTHETIC_ID, company="Fictional Pipeline Employer",
                  isactive="True", tpoprogram=CE, organization="VIT")
    values.update(changes)
    return CompanyRecord(**values)


@pytest.fixture
def detector(audit_repo):
    audit_repo.set_baseline_initialized()
    return CompanyDetector(audit_repo)


def test_conflicting_duplicate_identity_rejects_whole_observation(audit_repo, detector):
    audit_repo.upsert_company(rec())
    with pytest.raises(SourceEvidenceError):
        detector.process_fetched_companies(
            [rec(), rec(company="Fictional Impostor Employer")])
    assert audit_repo.get_company(SYNTHETIC_ID)["company"] == "Fictional Pipeline Employer"


def test_same_identity_conflicting_deadlines_reject_observation(audit_repo, detector):
    audit_repo.upsert_company(rec())
    with pytest.raises(SourceEvidenceError):
        detector.process_fetched_companies(
            [rec(regEnddate="15-Oct-2026"), rec(regEnddate="16-Oct-2026")])
    assert audit_repo.get_company(SYNTHETIC_ID)["registration_end"] is None


def test_identical_duplicates_deduplicate_to_one_record(audit_repo, detector):
    result = detector.process_fetched_companies([rec(), rec()])
    assert len(result.new_companies) == 1
    assert audit_repo.get_company(SYNTHETIC_ID) is not None
    assert len(audit_repo.get_pending_notifications()) == 1


@pytest.mark.parametrize("bad_name", ["", "   ", " \t\n "])
def test_blank_company_name_rejects_observation(audit_repo, detector, bad_name):
    with pytest.raises(SourceEvidenceError):
        detector.process_fetched_companies([rec(company=bad_name)])
    assert audit_repo.get_company(SYNTHETIC_ID) is None


@pytest.mark.parametrize("bad_id", ["", "   ", " null ", "None", " pad ", "x" * 257])
def test_unstable_identity_rejects_observation(audit_repo, detector, bad_id):
    with pytest.raises(SourceEvidenceError):
        detector.process_fetched_companies([rec(id=bad_id)])
