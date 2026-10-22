"""Validated listing evidence. No credentials, network or implicit completeness."""
from dataclasses import asdict, dataclass
import json
import re
from typing import Any

from app.tpo.models import CompanyRecord


class SourceEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ListingContract:
    source_namespace: str
    institution: str
    cohort: int
    evidence_reference: str
    freshness_ms: int
    allow_complete: bool = False
    source_timezone: str | None = None
    source_timezone_evidence: str | None = None

    def __post_init__(self):
        if (not isinstance(self.source_namespace, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.source_namespace)
                or self.institution != "VIT" or type(self.cohort) is not int or self.cohort != 2028
                or not isinstance(self.evidence_reference, str) or not 1 <= len(self.evidence_reference.strip()) <= 256
                or type(self.freshness_ms) is not int or not 1 <= self.freshness_ms <= 86400000
                or type(self.allow_complete) is not bool):
            raise ValueError("explicit supported source scope and bounded freshness required")
        if bool(self.source_timezone) != bool(self.source_timezone_evidence):
            raise ValueError("timezone requires independent field/scope evidence")
        if self.source_timezone is not None:
            from zoneinfo import ZoneInfo
            ZoneInfo(self.source_timezone)

    def to_wire(self):
        return asdict(self)


@dataclass(frozen=True)
class ListingPage:
    body: dict[str, Any]
    number: int
    total: int
    scope: str
    authenticated: bool


@dataclass(frozen=True)
class ListingObservation:
    records: tuple[CompanyRecord, ...]
    contract: ListingContract
    observed_ms: int
    complete: bool

    def __post_init__(self):
        if (not isinstance(self.contract,ListingContract) or type(self.observed_ms) is not int
                or self.observed_ms<0 or type(self.complete) is not bool
                or (self.complete and not self.contract.allow_complete)
                or not isinstance(self.records,tuple) or len(self.records)>2000
                or any(not isinstance(record,CompanyRecord) for record in self.records)):
            raise SourceEvidenceError("bounded typed observation and explicit completeness authority required")
        validate_listing_body({"company_list":[record.model_dump() for record in self.records]})
        if len({record.id for record in self.records})!=len(self.records):
            raise SourceEvidenceError("observation must contain unique source identities")


def validate_listing_body(body):
    if (not isinstance(body,dict) or body.get("error")
            or body.get("status") not in (None,"success","Success",True,200,"200")
            or not isinstance(body.get("company_list"),list) or len(body["company_list"])>2000):
        raise SourceEvidenceError("explicit successful company_list required")
    for raw in body["company_list"]:
        identity=raw.get("id") if isinstance(raw,dict) else None
        if (type(identity) not in (str,int) or (type(identity) is int and identity<0)
                or not str(identity).strip() or str(identity).strip().casefold() in ("null","none")
                or str(identity)!=str(identity).strip() or len(str(identity))>256):
            raise SourceEvidenceError("stable source identity required before coercion")


def decode_listing_pages(pages, *, contract, observed_ms, complete=False):
    if (not isinstance(contract, ListingContract) or type(observed_ms) is not int or observed_ms < 0
            or type(complete) is not bool or not isinstance(pages, (list, tuple))
            or not 1 <= len(pages) <= 100):
        raise SourceEvidenceError("bounded authenticated listing pages required")
    if complete and not contract.allow_complete:
        raise SourceEvidenceError("complete census is not authorized by this contract")
    records, numbers, total = {}, set(), None
    for page in pages:
        if (not isinstance(page, ListingPage) or page.authenticated is not True
                or page.scope != contract.source_namespace or type(page.number) is not int
                or type(page.total) is not int or not 1 <= page.number <= page.total <= 100
                or page.number in numbers or (total is not None and total != page.total)):
            raise SourceEvidenceError("page identity, authentication or scope is inconsistent")
        numbers.add(page.number)
        total = page.total
        body = page.body
        validate_listing_body(body)
        for raw in body["company_list"]:
            try:
                record = CompanyRecord(**raw)
            except (ValueError, TypeError) as exc:
                raise SourceEvidenceError("invalid company representation") from exc
            if not record.company.strip():
                raise SourceEvidenceError("company name required")
            encoded = json.dumps(record.model_dump(), sort_keys=True, separators=(",", ":"))
            if record.id in records and records[record.id][0] != encoded:
                raise SourceEvidenceError("conflicting duplicate source identity")
            records[record.id] = (encoded, record)
            if len(records) > 2000:
                raise SourceEvidenceError("listing envelope exceeded")
    if complete and numbers != set(range(1, total + 1)):
        raise SourceEvidenceError("all pages of the same scoped census required")
    return ListingObservation(tuple(value[1] for value in records.values()), contract, observed_ms, complete)
