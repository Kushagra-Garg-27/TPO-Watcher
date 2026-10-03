import logging
from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.database.repository import DatabaseRepository, DB_PATH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Opportunities"])


class OpportunityResponse(BaseModel):
    id: str
    company: str
    max_package: Optional[str] = None
    min_package: Optional[str] = None
    placement_type: Optional[str] = None
    registration_end: Optional[str] = None
    eligible_programs: Optional[str] = None
    company_type: Optional[str] = None
    is_active: Optional[str] = None
    first_seen_at: Optional[str] = None


@router.get("/opportunities", response_model=List[OpportunityResponse])
def get_current_opportunities() -> List[OpportunityResponse]:
    """
    Public read-only endpoint returning currently active TPO opportunities
    from the authoritative companies table (up to 5 records).
    """
    repo = DatabaseRepository(DB_PATH)
    records = repo.get_active_opportunities(limit=5)
    return [OpportunityResponse(**rec) for rec in records]
