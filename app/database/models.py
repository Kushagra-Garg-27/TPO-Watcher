import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import datetime

EMAIL_REGEX = re.compile(r"^[\w\.-]+@([\w\.-]+\.)+[\w-]{2,}$")

class UserCreate(BaseModel):
    model_config = {"extra": "forbid"}

    email: str = Field(..., max_length=254)
    graduation_year: int = Field(default=2028)
    branch_canonical: str = Field(..., max_length=64)
    pref_internship: bool = True
    pref_placement: bool = True
    pref_ppo: bool = True

    @field_validator("graduation_year")
    @classmethod
    def validate_graduation_year(cls, v: int) -> int:
        if v != 2028:
            raise ValueError("Public V1 is exclusively restricted to graduation year 2028.")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Email cannot be empty.")
        norm = v.strip().lower()
        if len(norm) > 254:
            raise ValueError("Email exceeds maximum allowed length of 254 characters.")
        if any(c in norm for c in ('\r', '\n', '\0')):
            raise ValueError("Email contains forbidden control characters.")
        if not EMAIL_REGEX.match(norm):
            raise ValueError("Invalid email format.")
        return norm

    @field_validator("branch_canonical")
    @classmethod
    def validate_branch(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Branch cannot be empty.")
        norm = v.strip()
        if len(norm) > 64:
            raise ValueError("Branch identifier exceeds maximum length of 64 characters.")
        return norm

class UserResponse(BaseModel):
    id: int
    email: str
    graduation_year: int
    branch_canonical: str
    is_verified: bool
    is_active: bool
    created_at: datetime
    verified_at: Optional[datetime] = None

class PreferenceUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    pref_internship: bool
    pref_placement: bool
    pref_ppo: bool

class PreferenceResponse(BaseModel):
    model_config = {"extra": "forbid"}

    user_id: int
    pref_internship: bool
    pref_placement: bool
    pref_ppo: bool
    updated_at: datetime

class DeliveryRecord(BaseModel):
    id: int
    user_id: int
    company_id: str
    notification_type: str
    status: Literal["PENDING", "PROCESSING", "SENT", "FAILED"]
    lease_expires_at: Optional[datetime] = None
    attempt_count: int = 0
    max_attempts: int = 5
    last_attempt_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
