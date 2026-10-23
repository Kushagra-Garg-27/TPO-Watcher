import pytest
import json
from app.tpo.models import CompanyRecord
from app.monitoring.detector import CompanyDetector
from app.database.repository import DatabaseRepository

class MockDB:
    def __init__(self):
        self.companies = {}
        self.baseline_init = False
        self.is_baseline_initialized_call_count = 0

    def is_baseline_initialized(self):
        self.is_baseline_initialized_call_count += 1
        return self.baseline_init

    def set_baseline_initialized(self):
        self.baseline_init = True

    def get_company(self, id):
        return self.companies.get(id)

    def upsert_company(self, record, is_new=False):
        # mock upsert
        self.companies[record.id] = {
            "id": record.id,
            "company": record.company,
            "registration_start": record.regStartdate,
            "max_package": record.maxPackage
        }

def test_baseline_initialization():
    db = MockDB()
    detector = CompanyDetector(db)
    
    companies = [
        CompanyRecord(id="1", company="A"),
        CompanyRecord(id="2", company="B")
    ]
    
    result = detector.process_fetched_companies(companies)
    
    assert len(result.new_companies) == 0
    assert len(result.updated_companies) == 0
    assert db.baseline_init == True
    assert "1" in db.companies
    assert "2" in db.companies

def test_new_company_detection():
    db = MockDB()
    db.baseline_init = True
    db.companies = {
        "1": {"id": "1", "company": "A"}
    }
    detector = CompanyDetector(db)
    
    companies = [
        CompanyRecord(id="1", company="A"),
        CompanyRecord(id="2", company="B")
    ]
    
    result = detector.process_fetched_companies(companies)
    
    assert len(result.new_companies) == 1
    assert result.new_companies[0].id == "2"
    assert len(result.updated_companies) == 0

def test_meaningful_change_detection():
    db = MockDB()
    db.baseline_init = True
    db.companies = {
        "1": {"id": "1", "company": "A", "registration_start": "01-Jan", "max_package": "10"}
    }
    detector = CompanyDetector(db)
    
    # maxPackage changed
    companies = [
        CompanyRecord(id="1", company="A", regStartdate="01-Jan", maxPackage="12")
    ]
    
    result = detector.process_fetched_companies(companies)
    
    assert len(result.new_companies) == 0
    assert len(result.updated_companies) == 1
    assert result.updated_companies[0][0].id == "1"
    assert any("Max Package" in c for c in result.updated_companies[0][1])


@pytest.fixture
def b1_repo(tmp_path):
    return DatabaseRepository(str(tmp_path / "b1_detector.sqlite"))


def b1_record(**overrides):
    values = dict(id="synthetic-b1", company="Fictional B1", isactive="True",
                  regStartdate="13-Oct-2026", regStarttime="11:31",
                  regEnddate="14-Oct-2026", regEndtime="10:00", organization="VIT",
                  tpoprogram="VIT-BTech-Computer Engineering")
    values.update(overrides)
    return CompanyRecord(**values)


@pytest.mark.parametrize("before,after,want", [
    ({}, {"regStarttime": "12:31"}, 1),
    ({}, {"regEndtime": None}, 1),
    ({"regStarttime": None}, {"regStarttime": "00:00"}, 1),
    ({}, {"regEndtime": "invalid"}, 1),
    ({"regEndtime": "invalid"}, {"regEndtime": "different-invalid"}, 1),
    ({"regEndtime": "invalid"}, {"regEndtime": " invalid "}, 0),
    ({}, {"regStartdate": " 13-oct-2026 ", "regStarttime": "11:31:00"}, 0),
    ({}, {"regStartdate": "2026-10-13"}, 0),
    ({"regEnddatenew": "2026-10-14T10:00"},
     {"regEnddatenew": "2026-10-14T18:00"}, 1),
    ({"regEnddatenew": "2026-10-14T10:00"},
     {"regEnddatenew": "2026-10-14T10:00:00"}, 0),
    ({}, {"regEnddatenew": "2026-10-15"}, 1),
    ({"regEnddate": None, "regEndtime": None}, {"regEnddatenew": "2026-10-14"}, 1),
    ({"regEnddate": "2026-10-14T10:00:00+05:30", "regEndtime": None},
     {"regEnddate": "2026-10-14T04:30:00Z"}, 0),
    ({"regEnddate": "2026-10-14T10:00", "regEndtime": None},
     {"regEnddate": "2026-10-14T10:00+05:30"}, 1),
    ({}, {"organization": "VIT, VIIT"}, 1),
    ({}, {"programnew": "VIT-BTech-Information Technology"}, 1),
    ({"programnew": "VIT-BTech-Computer Engineering"}, {"programnew": None}, 1),
    ({"organization": "VIT, VIIT"}, {"organization": "viit ; vit"}, 0),
    ({"tpoprogram": "VIT-BTech-Computer Engineering; VIT-BTech-Information Technology"},
     {"tpoprogram": " vit - btech - information technology, VIT-BTech-Computer Engineering, VIT-BTech-Computer Engineering "}, 0),
    ({"programnew": "UNKNOWN-BTech-Computer Engineering"},
     {"programnew": "UNKNOWN-BTech-Information Technology"}, 1),
])
def test_b1_source_changes_and_cosmetic_replays(b1_repo, before, after, want):
    detector = CompanyDetector(b1_repo)
    original = b1_record(**before)
    assert not detector.process_fetched_companies([original]).updated_companies
    first_seen = b1_repo.get_company(original.id)["first_seen_at"]
    changed = original.model_copy(update=after)
    result = detector.process_fetched_companies([changed])
    assert len(result.updated_companies) == want
    assert not result.new_companies
    stored = b1_repo.get_company(original.id)
    assert stored["first_seen_at"] == first_seen
    assert json.loads(stored["raw_data_json"]) == changed.model_dump(mode="json")
    assert not detector.process_fetched_companies([changed]).updated_companies


@pytest.mark.parametrize("raw", [None, "invalid-json", "[]", "{}"])
def test_b1_unavailable_raw_history_does_not_invent_changes(raw):
    record = b1_record()
    existing = dict(registration_start=record.regStartdate, registration_end=record.regEnddate,
                    eligible_programs=record.tpoprogram, organizations="VIT", is_active="True",
                    raw_data_json=raw)
    assert CompanyDetector(MockDB())._get_meaningful_changes(existing, record) == []


def test_b1_preserves_empty_feed_absence_and_legacy_return(b1_repo):
    detector = CompanyDetector(b1_repo)
    record = b1_record()
    control = b1_record(id="synthetic-control")
    assert not detector.process_fetched_companies([record, control]).new_companies
    assert not detector.process_fetched_companies([]).deactivated_companies
    assert b1_repo.get_company(record.id)["is_active"] == "True"
    assert detector.process_fetched_companies([control]).deactivated_companies == [record.id]
    assert json.loads(b1_repo.get_company(record.id)["raw_data_json"])["isactive"] == "True"
    returned = detector.process_fetched_companies([record, control])
    assert not returned.new_companies
    assert len(returned.updated_companies) == 1
    assert len(returned.updated_companies[0][1]) == 1
    assert "Active Status" in returned.updated_companies[0][1][0]
    assert not detector.process_fetched_companies([record, control]).updated_companies


@pytest.mark.parametrize("value,clock,status,precision,provenance", [
    (None, False, "missing", None, None),
    ("14-Oct-2026", False, "date", "date", None),
    ("2026-10-14T10:00", False, "datetime", "minute", None),
    ("2026-10-14T10:00:00.125+05:30", False, "datetime", "fraction", "explicit-source-offset"),
    ("10:00:01", True, "time", "second", None),
    ("10:00+05:30", True, "time", "minute", "explicit-source-offset"),
    ("31-Feb-2026", False, "unparsed", None, None),
    ("14/10/2026", False, "unparsed", None, None),
    ("2026-10-14T25:00", False, "unparsed", None, None),
    ("10:00 IST", True, "unparsed", None, None),
    ("10:00+25:00", True, "unparsed", None, None),
    ("2026-10-14T10:00:00.1234567", False, "unparsed", None, None),
])
def test_b1_registration_uncertainty_precision_and_offset(value, clock, status, precision, provenance):
    from app.tpo.normalization import normalize_registration
    normalized = normalize_registration(value, clock=clock)
    assert (normalized.status, normalized.precision, normalized.timezone_provenance) == (status, precision, provenance)
    assert normalized.raw == value
    if status == "date":
        assert not hasattr(normalized.value, "hour")
    if status == "datetime" and provenance is None:
        assert normalized.value.tzinfo is None


@pytest.mark.parametrize("alternate,want", [
    (None, ()), ("2026-10-14", ()), ("2026-10-14T10:00", ()),
    ("2026-10-14T18:00", ("regEnd",)), ("2026-10-15", ("regEnd",)),
])
def test_b1_conflicting_alternatives_remain_observable(alternate, want):
    from app.tpo.normalization import registration_conflicts
    assert registration_conflicts(b1_record(regEnddatenew=alternate).model_dump()) == want


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+05:30", "9999-12-31T23:59:59-05:30"])
def test_b1_explicit_offset_boundary_dates_do_not_abort_detection(value):
    detector = CompanyDetector(MockDB())
    original = b1_record(regEnddate=value, regEndtime=None)
    existing = dict(raw_data_json=original.model_dump_json(), is_active="True")
    assert detector._get_meaningful_changes(existing, original) == []
    assert detector._get_meaningful_changes(existing, original.model_copy(update={"regEnddate": "2026-10-14"}))


def test_b1_equivalent_offset_instants_are_not_conflicting_alternatives():
    from app.tpo.normalization import registration_conflicts
    record = b1_record(regEnddate="2026-10-14T10:00+05:30", regEndtime=None,
                       regEnddatenew="2026-10-14T04:30Z")
    assert registration_conflicts(record.model_dump()) == ()


CE_B1F = "VIT-BTech-Computer Engineering"
IT_B1F = "VIT-BTech-Information Technology"


@pytest.mark.parametrize("history,column,primary,secondary,want", [
    ({}, CE_B1F, IT_B1F, None, 1),
    ({}, CE_B1F, CE_B1F, None, 0),
    ({"programnew": None}, CE_B1F, IT_B1F, None, 1),
    ({"tpoprogram": None}, CE_B1F, None, IT_B1F, 1),
    ({"programnew": ""}, CE_B1F, IT_B1F, "", 1),
    ({"tpoprogram": ""}, CE_B1F, "", IT_B1F, 1),
    ({"programnew": None}, CE_B1F, CE_B1F, None, 0),
    ({"tpoprogram": None}, CE_B1F, None, CE_B1F, 0),
    ({"tpoprogram": CE_B1F}, CE_B1F, IT_B1F, None, 1),
    ({"programnew": CE_B1F}, CE_B1F, IT_B1F, CE_B1F, 1),
    ({"tpoprogram": CE_B1F}, CE_B1F, CE_B1F, IT_B1F, 0),
    ({"programnew": CE_B1F}, IT_B1F, IT_B1F, CE_B1F, 0),
    ({"tpoprogram": CE_B1F}, IT_B1F, CE_B1F, None, 0),
    ({"tpoprogram": CE_B1F}, IT_B1F, IT_B1F, None, 1),
    ({"tpoprogram": None, "programnew": CE_B1F}, IT_B1F, None, CE_B1F, 0),
    ({"tpoprogram": CE_B1F, "programnew": None}, IT_B1F, CE_B1F, None, 0),
    ({"tpoprogram": CE_B1F, "programnew": None}, CE_B1F, CE_B1F, IT_B1F, 1),
    ({"tpoprogram": None}, None, None, None, 0),
    ({"programnew": CE_B1F}, None, None, CE_B1F, 0),
    ({"tpoprogram": None}, None, None, IT_B1F, 1),
    ({"programnew": CE_B1F}, "", None, CE_B1F, 0),
    ({"programnew": CE_B1F}, "   ", None, CE_B1F, 0),
])
def test_b1f_r01_partial_history_projection_and_known_fields(b1_repo, history, column, primary, secondary, want):
    detector = CompanyDetector(b1_repo)
    original = b1_record(tpoprogram=CE_B1F, programnew=None)
    detector.process_fetched_companies([original])
    first_seen = b1_repo.get_company(original.id)["first_seen_at"]
    with b1_repo._get_conn() as conn:
        conn.execute("UPDATE companies SET raw_data_json=?, eligible_programs=? WHERE id=?",
                     (json.dumps(history), column, original.id))
        conn.commit()
    incoming = b1_record(tpoprogram=primary, programnew=secondary)
    result = detector.process_fetched_companies([incoming])
    assert len(result.updated_companies) == want
    assert not result.new_companies
    assert len(b1_repo.get_pending_notifications()) == want
    stored = b1_repo.get_company(original.id)
    assert stored["first_seen_at"] == first_seen
    assert json.loads(stored["raw_data_json"]) == incoming.model_dump(mode="json")
    assert not detector.process_fetched_companies([incoming]).updated_companies


def test_b1f_r01_unavailable_combined_column_does_not_invent_history():
    incoming = b1_record(tpoprogram=IT_B1F, programnew=None)
    existing = dict(raw_data_json='{"programnew": null}', organizations="VIT", is_active="True",
                    registration_start=incoming.regStartdate, registration_end=incoming.regEnddate)
    assert CompanyDetector(MockDB())._get_meaningful_changes(existing, incoming) == []


def test_b1f_r02_deep_legacy_json_does_not_abort_feed_or_persistence(b1_repo):
    detector = CompanyDetector(b1_repo)
    original = b1_record()
    detector.process_fetched_companies([original])
    first_seen = b1_repo.get_company(original.id)["first_seen_at"]
    # 20 KB is bounded, while 10,000 nesting levels exceed native parser limits.
    nested = "[" * 10_000 + "]" * 10_000
    with b1_repo._get_conn() as conn:
        conn.execute("UPDATE companies SET raw_data_json=? WHERE id=?", (nested, original.id))
        conn.commit()
    other = b1_record(id="synthetic-b1f-next")
    result = detector.process_fetched_companies([original, other])
    assert not result.updated_companies
    assert [record.id for record in result.new_companies] == [other.id]
    stored = b1_repo.get_company(original.id)
    assert stored["first_seen_at"] == first_seen
    assert json.loads(stored["raw_data_json"]) == original.model_dump(mode="json")
    assert b1_repo.get_company(other.id)


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_b1f_r02_does_not_swallow_storage_errors(monkeypatch, failure):
    db = MockDB()
    def fail_storage(_company_id):
        raise failure("Synthetic storage failure")
    monkeypatch.setattr(db, "get_company", fail_storage)
    with pytest.raises(failure, match="Synthetic storage failure"):
        CompanyDetector(db).process_fetched_companies([b1_record()])


def test_b1f_r03_native_parser_evidence():
    import sys
    from datetime import datetime, time
    offsets = ("+05:30", "+00:00", "-05:30", "+05:60", "-00:60", "+05:99",
               "+25:00", "+23:59", "-23:59", "+24:00")
    evidence = {"python": sys.version.split()[0], "parsers": {}}
    for name, parser, prefix in (("datetime", datetime.fromisoformat, "2026-10-14T10:00"),
                                 ("time", time.fromisoformat, "10:00")):
        results = {}
        for offset in offsets:
            try:
                value = parser(prefix + offset)
            except ValueError:
                results[offset] = "REJECTED"
            else:
                results[offset] = value.isoformat()
        evidence["parsers"][name] = results
        assert results["+05:30"].endswith("+05:30")
        assert results["+00:00"].endswith("+00:00")
        assert results["-05:30"].endswith("-05:30")
    print("B1F_NATIVE_PARSER_EVIDENCE: " + json.dumps(evidence, sort_keys=True))


@pytest.mark.parametrize("clock", [False, True])
@pytest.mark.parametrize("offset,seconds", [
    ("+05:30", 19800), ("+00:00", 0), ("-05:30", -19800), ("Z", 0),
    ("+23:59", 86340), ("-23:59", -86340),
    ("+05:60", None), ("-00:60", None), ("+05:99", None),
    ("+25:00", None), ("+24:00", None), ("-24:00", None),
])
def test_b1f_r03_explicit_offset_component_ranges(clock, offset, seconds):
    from app.tpo.normalization import normalize_registration
    raw = ("10:00" if clock else "2026-10-14T10:00") + offset
    normalized = normalize_registration(raw, clock=clock)
    assert normalized.raw == raw
    if seconds is None:
        assert normalized.status == "unparsed"
        assert normalized.timezone_provenance is None
    else:
        assert normalized.status == ("time" if clock else "datetime")
        assert normalized.value.utcoffset().total_seconds() == seconds
        assert normalized.timezone_provenance == "explicit-source-offset"


@pytest.mark.parametrize("valid,invalid", [("+06:00", "+05:60"), ("-01:00", "-00:60"), ("+06:39", "+05:99")])
def test_b1f_r03_valid_to_malformed_offset_is_material(b1_repo, valid, invalid):
    detector = CompanyDetector(b1_repo)
    original = b1_record(regEnddate="2026-10-14T10:00" + valid, regEndtime=None)
    detector.process_fetched_companies([original])
    incoming = original.model_copy(update={"regEnddate": "2026-10-14T10:00" + invalid})
    assert len(detector.process_fetched_companies([incoming]).updated_companies) == 1
    assert not detector.process_fetched_companies([incoming]).updated_companies


@pytest.mark.parametrize("primary,clock,alternate,want", [
    ("2026-10-14", "10:00+05:30", "2026-10-14T04:30:00Z", ()),
    ("2026-10-14", "10:00+05:30", "2026-10-14T10:00:00Z", ("regEnd",)),
    ("2026-10-14", "00:30+02:00", "2026-10-13T22:30:00Z", ()),
    ("2026-10-14", "23:30-02:00", "2026-10-15T01:30:00Z", ()),
    ("2026-10-14", "10:00", "2026-10-14T18:00", ("regEnd",)),
    ("2026-10-14", "10:00", "2026-10-14T10:00Z", ()),
    ("2026-10-14", "10:00", "2026-10-14T04:30Z", ()),
    ("0001-01-01", "00:00+05:30", "0001-01-01T00:00+05:30", ()),
    ("9999-12-31", "23:59:59-05:30", "9999-12-31T23:59:59-05:30", ()),
    ("2026-10-14T10:00+05:30", "04:30Z", None, ()),
    ("2026-10-14T10:00+05:30", "10:00Z", None, ("regEnd",)),
    ("2026-10-14T10:00+05:30", "10:00", "2026-10-14T04:30Z", ()),
    ("2026-10-14", None, "2026-10-14", ()),
    ("2026-10-14", None, "2026-10-15", ("regEnd",)),
])
def test_b1f_r04_separate_clock_offsets_and_uncertainty(primary, clock, alternate, want):
    from app.tpo.normalization import registration_conflicts, registration_values
    source = b1_record(regEnddate=primary, regEndtime=clock, regEnddatenew=alternate).model_dump()
    assert registration_conflicts(source) == want
    values = registration_values(source)
    # No conflict on a mixed pair means uncertainty, never verified equality.
    if clock == "10:00" and alternate and alternate.endswith("Z"):
        assert values["regEndtime"].value.tzinfo is None
        assert values["regEnddatenew"].timezone_provenance == "explicit-source-offset"
