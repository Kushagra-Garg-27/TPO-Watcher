from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import List, Optional, Any, Union

class CompanyRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    
    id: str
    company: str
    company_code: Optional[str] = None
    regStartdate: Optional[str] = None
    regStarttime: Optional[str] = None
    regEnddate: Optional[str] = None
    regEndtime: Optional[str] = None
    regStartdatenew: Optional[str] = None
    regEnddatenew: Optional[str] = None
    maxPackage: Optional[str] = None
    minPackage: Optional[str] = None
    placementtype: Optional[str] = None
    academicyear: Optional[str] = None
    companytype: Optional[str] = None
    skill: Optional[str] = None
    internshiptype: Optional[str] = None
    isactive: Optional[str] = None
    tpoprogram: Optional[str] = None
    programnew: Optional[str] = None
    organization: Optional[str] = None
    contact_person_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    job_description: Optional[str] = None
    locations: Optional[str] = None
    placement_process_locations: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: Any) -> str:
        return str(v)

    @field_validator("tpoprogram", "programnew", "organization", "skill", mode="before")
    @classmethod
    def coerce_list(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x is not None)
        return str(v)

    @field_validator("maxPackage", "minPackage", "isactive", "company_code", mode="before")
    @classmethod
    def coerce_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

class APIResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Optional[str] = None
    company_list: List[CompanyRecord] = Field(default_factory=list)
