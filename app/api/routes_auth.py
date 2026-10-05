import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings
from app.database.models import UserCreate, ConfirmTokenPayload
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

@router.post("/auth/verify/confirm")
def verify_email_confirm(
    request: Request,
    payload: ConfirmTokenPayload,
    repos = Depends(get_db_repos)
):
    """
    Explicit, non-idempotent confirm-before-consume verification endpoint.
    Consumes token and activates user subscription ONLY upon intentional user POST.
    Rejects query-string tokens.
    """
    if "token" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token must be passed in JSON body, not query string."
        )

    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"verify:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_VERIFY_PER_IP", 30),
        window_seconds=60
    )

    fingerprint = get_token_fingerprint(payload.token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    user_id = token_repo.claim_and_verify(token_hash)

    if not user_id:
        logger.warning(f"Verification confirm failed: token not found or already consumed (fingerprint: {fingerprint}).")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link."
        )

    logger.info(f"Email verified successfully for user {user_id} (fingerprint: {fingerprint}).")

    return {
        "status": "success",
        "message": "Email verified successfully! You will now receive verified TPO alerts."
    }


@router.get("/auth/verify")
def verify_email_legacy(
    request: Request,
    token: str = Query(..., description="One-time verification token")
):
    """
    Legacy verification link redirector.
    Harmless, non-mutating 303 redirect to the SPA verify view with URL fragment.
    Does NOT validate, consume, or reveal token validity.
    """
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"verify:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_VERIFY_PER_IP", 30),
        window_seconds=60
    )

    return RedirectResponse(
        url=f"/verify#token={token}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/unsubscribe/confirm")
def unsubscribe_confirm(
    request: Request,
    payload: ConfirmTokenPayload,
    repos = Depends(get_db_repos)
):
    """
    Explicit user-initiated unsubscribe confirm endpoint.
    Consumes token, deactivates user, and revokes pending deliveries upon intentional user POST.
    Rejects query-string tokens.
    """
    if "token" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token must be passed in JSON body, not query string."
        )

    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"unsub:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_UNSUBSCRIBE_PER_IP", 30),
        window_seconds=60
    )

    fingerprint = get_token_fingerprint(payload.token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    user_id = token_repo.claim_and_unsubscribe(token_hash)

    if not user_id:
        logger.warning(f"Unsubscribe confirm failed: token not found or already consumed (fingerprint: {fingerprint}).")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link."
        )

    logger.info(f"User {user_id} unsubscribed successfully (fingerprint: {fingerprint}).")

    return {
        "status": "success",
        "message": "Successfully unsubscribed from VIT TPO alerts."
    }


@router.get("/unsubscribe")
def unsubscribe_legacy_get(
    request: Request,
    token: str = Query(..., description="One-time unsubscribe token")
):
    """
    Legacy unsubscribe GET link redirector.
    Harmless, non-mutating 303 redirect to the SPA unsubscribe view with URL fragment.
    Does NOT validate, consume, or deactivate user.
    """
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"unsub:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_UNSUBSCRIBE_PER_IP", 30),
        window_seconds=60
    )

    return RedirectResponse(
        url=f"/unsubscribe#token={token}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_legacy_post(
    request: Request,
    token: str = Query(..., description="One-time unsubscribe token"),
    repos = Depends(get_db_repos)
):
    """
    Legacy direct POST unsubscribe endpoint preserved for backward compatibility.
    """
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"unsub:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_UNSUBSCRIBE_PER_IP", 30),
        window_seconds=60
    )

    if not is_valid_token_format(token):
        logger.warning("Unsubscribe rejected: malformed token format.")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Unsubscribe Link</h2><p>This unsubscribe link is invalid or has already been used.</p>"
        )

    fingerprint = get_token_fingerprint(token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    user_id = token_repo.claim_and_unsubscribe(token_hash)

    if not user_id:
        logger.warning(f"Unsubscribe failed: token not found or already consumed (fingerprint: {fingerprint}).")
        return spa_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            fallback_text="<h2>Invalid or Expired Unsubscribe Link</h2><p>This unsubscribe link is invalid or has already been used.</p>"
        )

    logger.info(f"User {user_id} unsubscribed successfully via legacy POST (fingerprint: {fingerprint}).")

    return spa_response(
        status_code=status.HTTP_200_OK,
        fallback_text="<h2>Unsubscribed Successfully</h2><p>You have been unsubscribed from VIT TPO email alerts. Any pending notifications have been cancelled.</p>"
    )


@router.post("/unsubscribe/one-click")
async def unsubscribe_one_click(
    request: Request,
    token: str = Query(..., description="One-click unsubscribe action token"),
    repos = Depends(get_db_repos)
):
    """
    RFC 8058 One-Click Unsubscribe endpoint.
    Accepts List-Unsubscribe=One-Click via application/x-www-form-urlencoded or multipart/form-data.
    Requires no user session or authentication. Does not redirect.
    """
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="replace").strip()

    valid_one_click = False
    if "list-unsubscribe=one-click" in body_str.lower():
        valid_one_click = True
    else:
        try:
            from urllib.parse import parse_qs
            form = parse_qs(body_str)
            for k, vals in form.items():
                if k.lower() == "list-unsubscribe" and any(v.lower() == "one-click" for v in vals):
                    valid_one_click = True
                    break
        except Exception:
            pass

    if not valid_one_click:
        logger.warning("RFC 8058 rejection: missing or invalid List-Unsubscribe=One-Click body.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid List-Unsubscribe=One-Click body."
        )

    if not is_valid_token_format(token):
        logger.warning("RFC 8058 rejection: malformed token format.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link."
        )

    fingerprint = get_token_fingerprint(token)
    user_repo, token_repo = repos

    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    user_id = token_repo.claim_and_unsubscribe(token_hash)

    if not user_id:
        logger.warning(f"RFC 8058 unsubscribe failed: token not found or already consumed (fingerprint: {fingerprint}).")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired unsubscribe link."
        )

    logger.info(f"User {user_id} unsubscribed via RFC 8058 one-click (fingerprint: {fingerprint}).")

    return {
        "status": "success",
        "message": "Successfully unsubscribed via RFC 8058 one-click."
    }


@router.get("/unsubscribe/one-click")
def unsubscribe_one_click_get():
    """
    RFC 8058 explicitly prohibits GET mutation.
    Returns 405 Method Not Allowed with zero mutation.
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Method Not Allowed"
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
