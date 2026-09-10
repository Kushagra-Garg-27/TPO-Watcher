import pytest
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
