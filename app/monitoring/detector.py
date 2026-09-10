import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from app.tpo.models import CompanyRecord
from app.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

@dataclass
class DetectionResult:
    new_companies: List[CompanyRecord] = field(default_factory=list)
    updated_companies: List[Tuple[CompanyRecord, List[str]]] = field(default_factory=list)

class CompanyDetector:
    def __init__(self, db: DatabaseRepository):
        self.db = db

    def process_fetched_companies(self, companies: List[CompanyRecord]) -> DetectionResult:
        is_baseline = not self.db.is_baseline_initialized()
        result = DetectionResult()
        
        if is_baseline:
            logger.info(f"Initializing baseline with {len(companies)} companies. No notifications will be sent.")
            
        for company in companies:
            existing = self.db.get_company(company.id)
            
            if not existing:
                # New company
                self.db.upsert_company(company, is_new=True)
                if not is_baseline:
                    result.new_companies.append(company)
                    if hasattr(self.db, "add_pending_notification"):
                        self.db.add_pending_notification(company.id, "NEW", [])
            else:
                # Existing company.
                # Update last_seen_at implicitly.
                if not is_baseline:
                    changes = self._get_meaningful_changes(existing, company)
                    if changes:
                        result.updated_companies.append((company, changes))
                        if hasattr(self.db, "add_pending_notification"):
                            self.db.add_pending_notification(company.id, "UPDATE", changes)
                
                # We always upsert to keep raw_data_json and last_seen_at fresh.
                self.db.upsert_company(company)
                    
        if is_baseline:
            self.db.set_baseline_initialized()
            
        return result

    def _get_meaningful_changes(self, existing: dict, new_record: CompanyRecord) -> List[str]:
        changes = []
        
        fields_to_check = [
            ("registration_start", new_record.regStartdate, "Registration Start"),
            ("registration_end", new_record.regEnddate, "Registration End"),
            ("max_package", new_record.maxPackage, "Max Package"),
            ("min_package", new_record.minPackage, "Min Package"),
            ("placement_type", new_record.placementtype, "Placement Type"),
            ("internship_type", new_record.internshiptype, "Internship Type"),
            ("is_active", new_record.isactive, "Active Status")
        ]
        
        for db_key, new_val, readable_name in fields_to_check:
            old_val_str = str(existing.get(db_key, "")).strip() if existing.get(db_key) else ""
            new_val_str = str(new_val).strip() if new_val else ""
            
            if old_val_str != new_val_str and not (old_val_str == "None" and new_val_str == ""):
                changes.append(f"{readable_name}: '{old_val_str}' -> '{new_val_str}'")
                
        old_programs = str(existing.get("eligible_programs", "")).strip()
        new_programs = str(new_record.tpoprogram or new_record.programnew or "").strip()
        
        if old_programs != new_programs and not (old_programs == "None" and new_programs == ""):
            changes.append(f"Eligible Programs changed.")
            
        return changes
