import logging
from typing import List, Optional
from fastapi import APIRouter, Request, HTTPException
import time
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
def get_current_opportunities(request: Request) -> List[OpportunityResponse]:
    """
    Compatibility projection of stored active flags (up to five records).
    Use /opportunity-listing for verified listing freshness and scope.
    """
    from app.database.opportunity_projection import legacy_projection
    clock=getattr(request.app.state,"opportunity_clock",lambda:int(time.time()*1000))
    path=getattr(request.app.state,"opportunity_database",DB_PATH)
    try:records = legacy_projection(path,limit=5,now_ms=clock())
    except Exception:raise HTTPException(503,"Opportunity projection is unavailable") from None
    return [OpportunityResponse(**rec) for rec in records]


@router.get("/opportunity-listing")
def get_verified_listing(request: Request):
    """Fresh per-company evidence, rather than aggregate fetch/census health.

    Without any per-company observation, even an initial successful empty
    census conservatively returns UNVERIFIED. Historical rows never establish
    current listing evidence by themselves.
    """
    from app.database.opportunity_projection import listing_projection
    clock=getattr(request.app.state,"opportunity_clock",lambda:int(time.time()*1000))
    path=getattr(request.app.state,"opportunity_database",DB_PATH)
    try:
        return listing_projection(path,now_ms=clock())
    except (OSError,ValueError,RuntimeError):
        raise HTTPException(503,"Verified listing is unavailable") from None
    except Exception:
        # No raw source, database path, custody or private data in public errors.
        raise HTTPException(503,"Verified listing is unavailable") from None
