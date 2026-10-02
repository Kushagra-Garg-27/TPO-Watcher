import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.database.models import UserCreate
from app.database.interfaces import UserRepositoryProtocol, TokenRepositoryProtocol
from app.subscribers.canonical import CanonicalBranch
from app.api.email_service import EmailService, get_email_service
from app.api.rate_limiter import check_rate_limit, get_client_ip
from app.api.security_utils import is_valid_token_format, get_token_fingerprint
from app.api.session import revoke_session_token
from app.api.web_views import spa_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Authentication & Subscription"])

def get_db_repos():
    from app.database.repository import DB_PATH
    from app.database.sqlite_repository import SQLiteUserRepository, SQLiteTokenRepository
    return SQLiteUserRepository(DB_PATH), SQLiteTokenRepository(DB_PATH)

@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(
    request: Request,
    payload: UserCreate,
    repos = Depends(get_db_repos),
    email_service: EmailService = Depends(get_email_service)
):
    # 0. Enforce abuse rate limiting (per-IP and per-target-email)
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"signup:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_SIGNUP_PER_IP", 30),
        window_seconds=60
    )
    check_rate_limit(
        request=request,
        key=f"signup:email:{payload.email.lower()}",
        max_requests=getattr(settings, "RATE_LIMIT_SIGNUP_PER_EMAIL", 5),
        window_seconds=900,
        detail="Too many signup attempts for this email address. Please try again later."
    )

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
            # User is already verified.
            # Security Hardening: Normalize response to prevent email enumeration.
            # Invalidate older preference tokens and dispatch a fresh 15-minute preference access link
            # directly to the student's email inbox so legitimate users can manage preferences.
            token_repo.invalidate_user_tokens(existing["id"], "MANAGE_PREFS")
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            token_repo.create_token(
                user_id=existing["id"],
                token_hash=token_hash,
                token_type="MANAGE_PREFS",
                expires_at=expires_at
            )
            email_service.send_preference_link_email(payload.email, raw_token)
            logger.info(f"Existing verified user signup received; preference link dispatched. Fingerprint: {token_hash[:8]}")
            return {
                "status": "success",
                "message": "Verification link sent! Please check your email and click the link to activate notifications or manage preferences."
            }
        else:
            user_id = existing["id"]
            # Update preferences and branch if changed before verification
            user_repo.update_preferences(
                user_id=user_id,
                pref_internship=payload.pref_internship,
                pref_placement=payload.pref_placement,
                pref_ppo=payload.pref_ppo
            )
            # Invalidate any older signup tokens (supersession)
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

    # 4. Generate 24-hour verification token with 256 bits of CSPRNG entropy
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
    else:
        logger.info(f"Verification email dispatched for user {user_id}. Fingerprint: {token_hash[:8]}")

    return {
        "status": "success",
        "message": "Verification link sent! Please check your email and click the link within 24 hours to activate notifications."
    }

@router.get("/auth/verify", response_class=HTMLResponse)
def verify_email(
    request: Request,
    token: str = Query(..., description="One-time verification token"),
    repos = Depends(get_db_repos)
):
    # 0. Rate limiting to prevent token brute force
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"verify:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_VERIFY_PER_IP", 30),
        window_seconds=60
    )

    # 1. Early input validation
    if not is_valid_token_format(token):
        logger.warning("Verification rejected: malformed token format.")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Link</h2><p>This verification link is invalid, already used, or has expired after 24 hours.</p>"
        )

    fingerprint = get_token_fingerprint(token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    token_row = token_repo.get_valid_token(token_hash, "SIGNUP_VERIFY")

    if not token_row:
        logger.warning(f"Verification failed: token not found or already consumed (fingerprint: {fingerprint}).")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Link</h2><p>This verification link is invalid, already used, or has expired after 24 hours.</p>"
        )

    # Mark user verified and token used (one-time use)
    user_repo.set_verified(token_row["user_id"])
    token_repo.mark_token_used(token_row["id"])
    logger.info(f"Email verified successfully for user {token_row['user_id']} (fingerprint: {fingerprint}).")

    return spa_response(
        status_code=status.HTTP_200_OK,
        fallback_text="<h2>✓ Email Verified!</h2><p>Your email subscription for VIT Pune 2028 TPO Alerts is now active.</p>"
    )


@router.get("/unsubscribe", response_class=HTMLResponse)
@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(
    request: Request,
    token: str = Query(..., description="One-time unsubscribe token"),
    repos = Depends(get_db_repos)
):
    # 0. Rate limiting to prevent token abuse
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"unsub:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_UNSUBSCRIBE_PER_IP", 30),
        window_seconds=60
    )

    # 1. Early input validation
    if not is_valid_token_format(token):
        logger.warning("Unsubscribe rejected: malformed token format.")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Unsubscribe Link</h2><p>This unsubscribe link is invalid or has already been used.</p>"
        )

    fingerprint = get_token_fingerprint(token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    token_row = token_repo.get_valid_token(token_hash, "UNSUBSCRIBE")

    if not token_row:
        logger.warning(f"Unsubscribe failed: token not found or already consumed (fingerprint: {fingerprint}).")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Unsubscribe Link</h2><p>This unsubscribe link is invalid or has already been used.</p>"
        )

    # Immediately unsubscribe user and cancel pending deliveries
    user_repo.set_unsubscribed(token_row["user_id"])
    token_repo.mark_token_used(token_row["id"])
    logger.info(f"User {token_row['user_id']} unsubscribed successfully (fingerprint: {fingerprint}).")

    return spa_response(
        status_code=status.HTTP_200_OK,
        fallback_text="<h2>Unsubscribed Successfully</h2><p>You have been unsubscribed from VIT TPO email alerts. Any pending notifications have been cancelled.</p>"
    )


@router.post("/auth/logout")
def logout(
    response: Response,
    request: Request
):
    """
    Explicitly invalidates the session:
    1. Revokes the session token server-side.
    2. Clears the HttpOnly session cookie on the client.
    """
    tpo_session = request.cookies.get("tpo_session")
    if tpo_session:
        revoke_session_token(tpo_session)
        logger.info("Session revoked upon logout.")
    response.delete_cookie(
        key="tpo_session",
        path="/",
        httponly=True,
        samesite="lax"
    )
    return {"status": "success", "message": "Successfully logged out."}
