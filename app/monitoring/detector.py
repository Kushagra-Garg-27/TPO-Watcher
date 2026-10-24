import logging
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from app.tpo.models import CompanyRecord
from app.database.repository import DatabaseRepository
from app.tpo.listing import SourceEvidenceError
from app.tpo.normalization import registration_values, normalize_organizations, tokenize_programmes
from app.subscribers.canonical import extract_eligible_canonical_branches

logger = logging.getLogger(__name__)

@dataclass
class DetectionResult:
    new_companies: List[CompanyRecord] = field(default_factory=list)
    updated_companies: List[Tuple[CompanyRecord, List[str]]] = field(default_factory=list)
    deactivated_companies: List[str] = field(default_factory=list)

class CompanyDetector:
    def __init__(self, db: DatabaseRepository):
        self.db = db

    @staticmethod
    def _validated_unique_records(companies: List[CompanyRecord]) -> List[CompanyRecord]:
        """F09: whole-observation identity/name checks before any business write.

        Identical duplicate records collapse to their first occurrence; a
        conflicting duplicate identity, a blank/whitespace name or an unstable
        identity rejects the entire observation (mirroring the stricter listing
        adapter's contract: no writes happen on a rejected observation)."""
        unique: Dict[str, tuple] = {}
        ordered: List[CompanyRecord] = []
        for record in companies:
            identity = str(record.id)
            if (not identity.strip() or identity.strip().casefold() in ("null", "none")
                    or identity != identity.strip() or len(identity) > 256):
                raise SourceEvidenceError("stable source identity required")
            if not str(record.company or "").strip():
                raise SourceEvidenceError("company name required")
            encoded = json.dumps(record.model_dump(), sort_keys=True, separators=(",", ":"))
            if identity in unique:
                if unique[identity] != encoded:
                    raise SourceEvidenceError("conflicting duplicate source identity")
                continue
            unique[identity] = encoded
            ordered.append(record)
        return ordered

    def _apply(self, company: CompanyRecord, *, is_new: bool,
               notification_type: Optional[str] = None, changes: Optional[List[str]] = None):
        """F05: prefer the atomic company-transition+obligation unit. Duck-typed
        repositories without apply_company_event keep the legacy calls."""
        apply_event = getattr(self.db, "apply_company_event", None)
        if callable(apply_event):
            apply_event(company, is_new=is_new, notification_type=notification_type,
                        changes=changes, event_snapshot=company.model_dump_json())
            return
        self.db.upsert_company(company, is_new=is_new)
        if notification_type is not None and hasattr(self.db, "add_pending_notification"):
            self.db.add_pending_notification(company.id, notification_type, changes or [])

    def process_fetched_companies(self, companies: List[CompanyRecord],
                                  *, observation_complete: bool = False) -> DetectionResult:
        companies = self._validated_unique_records(list(companies))
        is_baseline = not self.db.is_baseline_initialized()
        result = DetectionResult()

        if is_baseline:
            logger.info(f"Initializing baseline with {len(companies)} companies. No notifications will be sent.")

        for company in companies:
            existing = self.db.get_company(company.id)

            if not existing:
                # New company. Baseline ingest records the company without any
                # notification obligation; a detected new company records both
                # atomically.
                if is_baseline:
                    self._apply(company, is_new=True)
                else:
                    self._apply(company, is_new=True, notification_type="NEW", changes=[])
                    result.new_companies.append(company)
            else:
                # Existing company. Update last_seen_at implicitly.
                if not is_baseline:
                    changes = self._get_meaningful_changes(existing, company)
                    if changes:
                        # F05: the stored update and its durable UPDATE obligation
                        # commit together, with the event-time snapshot (F07).
                        self._apply(company, is_new=False, notification_type="UPDATE", changes=changes)
                        result.updated_companies.append((company, changes))
                    else:
                        self._apply(company, is_new=False)
                else:
                    self._apply(company, is_new=False)

        # Authoritative Feed Reconciliation — F02: absence evidence requires an
        # explicitly complete observation. A non-empty but possibly partial feed
        # must never fabricate absence; positive updates above are unaffected.
        # Default V1 has no completeness authority, so it fails closed.
        if companies and observation_complete is True:
            if hasattr(self.db, "deactivate_missing_companies"):
                incoming_ids = {str(c.id) for c in companies}
                deactivated = self.db.deactivate_missing_companies(incoming_ids)
                result.deactivated_companies = deactivated
        elif companies:
            logger.info(
                "Observation carries no completeness authority; preserving stored "
                "companies absent from this feed (feed size %d).", len(companies)
            )
        else:
            logger.warning(
                "process_fetched_companies received empty companies list. "
                "Preserving existing active database state in accordance with safety invariants."
            )

        if is_baseline:
            self.db.set_baseline_initialized()

        return result

    def _get_meaningful_changes(self, existing: dict, new_record: CompanyRecord) -> List[str]:
        return self.meaningful_changes(existing, new_record)

    @staticmethod
    def meaningful_changes(existing: dict, new_record: CompanyRecord) -> List[str]:
        changes = []
        
        fields_to_check = [
            ("max_package", new_record.maxPackage, "Max Package"),
            ("min_package", new_record.minPackage, "Min Package"),
            ("placement_type", new_record.placementtype, "Placement Type"),
            ("internship_type", new_record.internshiptype, "Internship Type"),
            ("is_active", new_record.isactive, "Active Status")
        ]
        
        for db_key, new_val, readable_name in fields_to_check:
            if db_key == "is_active":
                val_str = str(new_val).strip().lower() if new_val is not None else ""
                # An absent or unknown status from the portal is NOT a confirmed lifecycle transition.
                # Only explicitly declared active/inactive statuses trigger an Active Status change.
                if val_str in ("true", "1", "yes", "false", "0", "no"):
                    old_active = "True" if str(existing.get(db_key, "")).strip().lower() in ("true", "1") else "False"
                    new_active = "True" if val_str in ("true", "1", "yes") else "False"
                    if old_active != new_active:
                        changes.append(f"{readable_name}: '{old_active}' -> '{new_active}'")
                continue

            old_val_str = str(existing.get(db_key, "")).strip() if existing.get(db_key) else ""
            new_val_str = str(new_val).strip() if new_val else ""
            
            if old_val_str != new_val_str and not (old_val_str == "None" and new_val_str == ""):
                changes.append(f"{readable_name}: '{old_val_str}' -> '{new_val_str}'")
                
        # Full source history carries the independent time and programme fields.
        # Missing/corrupt history is unavailable, not an invented empty payload.
        try:
            previous = json.loads(existing.get("raw_data_json") or "{}")
        except (ValueError, TypeError, RecursionError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        previous = dict(previous)
        for source_key, column in (("regStartdate", "registration_start"),
                                   ("regEnddate", "registration_end"),
                                   ("organization", "organizations")):
            if source_key not in previous and column in existing:
                previous[source_key] = existing[column]
        current = new_record.model_dump()
        old_registration = registration_values(previous)
        new_registration = registration_values(current)
        for key, old in old_registration.items():
            if old.signature != new_registration[key].signature:
                changes.append(f"Registration field {key} changed.")

        for key, normalizer in (("organization", normalize_organizations),
                                ("tpoprogram", tokenize_programmes),
                                ("programnew", tokenize_programmes)):
            if key in previous and normalizer(previous[key]).signature != normalizer(current[key]).signature:
                changes.append(f"Routing field {key} changed.")

        if not all(key in previous for key in ("tpoprogram", "programnew")):
            # Legacy eligible_programs lost which field supplied it. Compare
            # only the available combined evidence, never infer that provenance.
            if "eligible_programs" in existing:
                projection = existing["eligible_programs"]
                # A known nonempty primary determines the original repository
                # projection. A disagreeing legacy column cannot override it.
                if previous.get("tpoprogram"):
                    projection = previous["tpoprogram"]
                # An empty column cannot establish empty routing when a known
                # nonempty secondary survives. Keep that direct evidence;
                # Keep the independent secondary evidence; do not invent the
                # unavailable primary or a historical combined value.
                projection_missing = projection is None or (isinstance(projection, str) and not projection.strip())
                reliable = not (projection_missing and previous.get("programnew"))
                old_programs = tokenize_programmes(projection)
                new_programs = tokenize_programmes(new_record.tpoprogram or new_record.programnew)
                if reliable and old_programs.signature != new_programs.signature:
                    changes.append("Eligible Programs changed.")
        if all(key in previous for key in ("organization", "tpoprogram", "programnew")):
            old_branches = extract_eligible_canonical_branches(previous["tpoprogram"], previous["programnew"],
                                                             previous["organization"])
            new_branches = extract_eligible_canonical_branches(new_record.tpoprogram, new_record.programnew,
                                                             new_record.organization)
            if old_branches != new_branches:
                changes.append("Institution-qualified programme set changed.")

        return changes
