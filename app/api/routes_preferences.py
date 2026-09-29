import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, field_validator
from fastapi import APIRouter, Depends, HTTPException, Query, Cookie, Response, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.database.models import PreferenceUpdate, PreferenceResponse
from app.database.interfaces import UserRepositoryProtocol, TokenRepositoryProtocol
from app.api.session import create_session_token, verify_session_token
from app.api.email_service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Preference Management"])

def get_db_repos():
    from app.database.repository import DB_PATH
    from app.database.sqlite_repository import SQLiteUserRepository, SQLiteTokenRepository
    return SQLiteUserRepository(DB_PATH), SQLiteTokenRepository(DB_PATH)

def get_current_user_id(
    tpo_session: Optional[str] = Cookie(None)
) -> int:
    if not tpo_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session missing or expired. Please request a new preference access link."
        )
    user_id = verify_session_token(tpo_session, settings.SECRET_KEY)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please request a new preference access link."
        )
    return user_id

class RequestLinkPayload(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        import re
        norm = v.strip().lower()
        if not re.match(r"^[\w\.-]+@([\w\.-]+\.)+[\w-]{2,}$", norm):
            raise ValueError("Invalid email format.")
        return norm

@router.get("/preferences/request")
def exchange_magic_link_for_session(
    request: Request,
    token: str = Query(..., description="15-minute single-use action token"),
    repos = Depends(get_db_repos)
):
    """
    Exchanges a 15-minute action token for a 1-hour secure HttpOnly session cookie,
    ensuring no long-lived bearer tokens linger in browser history or URLs.
    """
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    token_row = token_repo.get_valid_token(token_hash, "MANAGE_PREFS")

    if not token_row:
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
    <h2 style="color: #d32f2f;">Invalid or Expired Link</h2>
    <p>This preference access link is invalid, already used, or expired (15-minute limit).</p>
    <p><a href="/preferences" style="color: #0366d6;">Request a New Link</a></p>
</body></html>""",
            status_code=status.HTTP_400_BAD_REQUEST
        )

    user_id = token_row["user_id"]
    # Mark token used immediately (single-use exchange)
    token_repo.mark_token_used(token_row["id"])

    # Create 1-hour session token
    session_token = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY, max_age_seconds=3600)

    is_secure = (
        bool(getattr(settings, "COOKIE_SECURE", False))
        or request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )

    # Redirect to preference management page with session cookie
    redirect = RedirectResponse(url="/preferences", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        key="tpo_session",
        value=session_token,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=is_secure
    )
    return redirect

@router.post("/preferences/request-link")
def request_preference_link(
    payload: RequestLinkPayload,
    repos = Depends(get_db_repos),
    email_service: EmailService = Depends(lambda: EmailService())
):
    """
    Requests a fresh 15-minute access link sent directly to the student's email.
    """
    user_repo, token_repo = repos
    user = user_repo.get_by_email(payload.email)

    if user and user["is_verified"] == 1 and user["is_active"] == 1:
        # Invalidate existing unused preference tokens
        token_repo.invalidate_user_tokens(user["id"], "MANAGE_PREFS")

        # Create 15-minute single-use token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        token_repo.create_token(
            user_id=user["id"],
            token_hash=token_hash,
            token_type="MANAGE_PREFS",
            expires_at=expires_at
        )

        email_service.send_preference_link_email(user["email"], raw_token)

    return {
        "status": "success",
        "message": "If an active account exists for this email, a 15-minute access link has been sent to your inbox."
    }

@router.get("/preferences")
def get_preferences(
    user_id: int = Depends(get_current_user_id),
    repos = Depends(get_db_repos)
):
    """
    Retrieves the current preferences for the authenticated session user.
    """
    user_repo, _ = repos
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    prefs = user_repo.get_preferences(user_id)
    return {
        "email": user["email"],
        "graduation_year": user["graduation_year"],
        "branch_canonical": user["branch_canonical"],
        "pref_internship": bool(prefs["pref_internship"]),
        "pref_placement": bool(prefs["pref_placement"]),
        "pref_ppo": bool(prefs["pref_ppo"])
    }

@router.put("/preferences")
def update_preferences(
    payload: PreferenceUpdate,
    user_id: int = Depends(get_current_user_id),
    repos = Depends(get_db_repos)
):
    """
    Updates the preferences for the authenticated session user.
    """
    user_repo, _ = repos
    user_repo.update_preferences(
        user_id=user_id,
        pref_internship=payload.pref_internship,
        pref_placement=payload.pref_placement,
        pref_ppo=payload.pref_ppo
    )
    return {
        "status": "success",
        "message": "Preferences updated successfully."
    }
