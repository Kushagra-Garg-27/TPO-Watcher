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
from app.api.session import revoke_session_token, perform_logout
from app.api.web_views import spa_response
from app.database.notification_compatibility import repository_factory, repository_unit, account_request_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Authentication & Subscription"])

def get_db_repos():
    from app.database.repository import DB_PATH
    return repository_factory(DB_PATH)

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
        key=f"signup:email:{hashlib.sha256(payload.email.lower().encode('utf-8')).hexdigest()}",
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

    # Identity/preferences, supersession and the new token commit together.
    with repository_unit(repos):
        existing = user_repo.get_by_email(payload.email)
        verified = existing is not None and existing["is_verified"] == 1
        if existing:
            user_id = existing["id"]
            if not verified:
                user_repo.update_preferences(user_id, payload.pref_internship, payload.pref_placement, payload.pref_ppo)
        else:
            user_id = user_repo.create_user(payload.email, 2028, payload.branch_canonical,
                payload.pref_internship, payload.pref_placement, payload.pref_ppo)
        from app.database.account_schema import account_policy_enabled
        production = False
        allowed = True
        if hasattr(user_repo, "_get_conn"):
            with user_repo._get_conn() as connection:
                production = account_policy_enabled(connection)
                if production:
                    from app.auth.accounts import AccountService
                    allowed = AccountService(connection)._account(user_id)["account_state"] == "ACTIVE"
        kind = ("ACCOUNT_LOGIN" if production else "MANAGE_PREFS") if verified else "SIGNUP_VERIFY"
        raw_token = None
        if allowed:
            token_repo.invalidate_user_tokens(user_id, kind)
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            token_repo.create_token(user_id, token_hash, kind,
                datetime.now(timezone.utc) + (timedelta(minutes=15) if verified else timedelta(hours=24)))

    # External transport starts only after the identity unit commits. A failure
    # or ambiguous outcome cannot alter the public account-existence contract.
    try:
        send = email_service.send_preference_link_email if verified else email_service.send_verification_email
        if raw_token is not None:
            send(payload.email, raw_token)
    except Exception:
        logger.warning("Account-link request could not complete transport processing.")
    legacy = ("Verification link sent! Please check your email and click the link to activate notifications or manage preferences."
        if verified else "Verification link sent! Please check your email and click the link within 24 hours to activate notifications.")
    return account_request_response(repos, legacy)

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
        "message": "Email ownership verified. Enrollment review, notification destination verification, and consent are required before production alerts."
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
    request: Request,
    repos = Depends(get_db_repos)
):
    """
    Canonical session logout endpoint:
    1. Revokes the session token server-side.
    2. Clears the HttpOnly session cookie on the client.
    """
    return perform_logout(request, response, repos=repos)
