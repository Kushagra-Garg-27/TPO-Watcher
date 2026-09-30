import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.database.models import UserCreate
from app.database.interfaces import UserRepositoryProtocol, TokenRepositoryProtocol
from app.subscribers.canonical import CanonicalBranch
from app.api.email_service import EmailService, get_email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Authentication & Subscription"])

def get_db_repos():
    from app.database.repository import DB_PATH
    from app.database.sqlite_repository import SQLiteUserRepository, SQLiteTokenRepository
    return SQLiteUserRepository(DB_PATH), SQLiteTokenRepository(DB_PATH)

@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(
    payload: UserCreate,
    repos = Depends(get_db_repos),
    email_service: EmailService = Depends(get_email_service)
):
    user_repo, token_repo = repos

    # 1. Enforce graduation year 2028
    if payload.graduation_year != 2028:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Public V1 is exclusively restricted to the VIT Pune Class of 2028."
        )

    # 2. Enforce canonical branch
    try:
        CanonicalBranch(payload.branch_canonical)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid branch '{payload.branch_canonical}'. Must be a recognized canonical VIT program."
        )

    # 3. Check existing user
    existing = user_repo.get_by_email(payload.email)
    if existing:
        if existing["is_verified"] == 1:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "exists",
                    "message": "This email is already registered and verified. You can manage your preferences using the link sent in your alert emails."
                }
            )
        else:
            user_id = existing["id"]
            # Update preferences and branch if changed before verification
            user_repo.update_preferences(
                user_id=user_id,
                pref_internship=payload.pref_internship,
                pref_placement=payload.pref_placement,
                pref_ppo=payload.pref_ppo
            )
            # Invalidate any older signup tokens
            token_repo.invalidate_user_tokens(user_id, "SIGNUP_VERIFY")
    else:
        # Create new unverified user
        user_id = user_repo.create_user(
            email=payload.email,
            graduation_year=2028,
            branch_canonical=payload.branch_canonical,
            pref_internship=payload.pref_internship,
            pref_placement=payload.pref_placement,
            pref_ppo=payload.pref_ppo
        )

    # 4. Generate 24-hour verification token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    token_repo.create_token(
        user_id=user_id,
        token_hash=token_hash,
        token_type="SIGNUP_VERIFY",
        expires_at=expires_at
    )

    # 5. Dispatch verification email
    sent = email_service.send_verification_email(payload.email, raw_token)
    if not sent:
        logger.warning(f"Could not deliver verification email to {payload.email}.")

    return {
        "status": "success",
        "message": "Verification link sent! Please check your email and click the link within 24 hours to activate notifications."
    }

@router.get("/auth/verify", response_class=HTMLResponse)
def verify_email(
    token: str = Query(..., description="One-time verification token"),
    repos = Depends(get_db_repos)
):
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    token_row = token_repo.get_valid_token(token_hash, "SIGNUP_VERIFY")

    from app.api.web_views import spa_response

    if not token_row:
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Link</h2><p>This verification link is invalid, already used, or has expired after 24 hours.</p>"
        )

    # Mark user verified and token used (one-time use)
    user_repo.set_verified(token_row["user_id"])
    token_repo.mark_token_used(token_row["id"])

    return spa_response(
        status_code=status.HTTP_200_OK,
        fallback_text="<h2>✓ Email Verified!</h2><p>Your email subscription for VIT Pune 2028 TPO Alerts is now active.</p>"
    )


@router.get("/unsubscribe", response_class=HTMLResponse)
@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    token: str = Query(..., description="One-time unsubscribe token"),
    repos = Depends(get_db_repos)
):
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    token_row = token_repo.get_valid_token(token_hash, "UNSUBSCRIBE")

    from app.api.web_views import spa_response

    if not token_row:
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Unsubscribe Link</h2><p>This unsubscribe link is invalid or has already been used.</p>"
        )

    # Immediately unsubscribe user and cancel pending deliveries
    user_repo.set_unsubscribed(token_row["user_id"])
    token_repo.mark_token_used(token_row["id"])

    return spa_response(
        status_code=status.HTTP_200_OK,
        fallback_text="<h2>Unsubscribed Successfully</h2><p>You have been unsubscribed from VIT TPO email alerts. Any pending notifications have been cancelled.</p>"
    )
