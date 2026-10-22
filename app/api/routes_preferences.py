import hashlib
import secrets
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, Query, Cookie, Response, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.database.models import PreferenceUpdate, PreferenceResponse, ConfirmTokenPayload
from app.database.interfaces import UserRepositoryProtocol, TokenRepositoryProtocol
from app.api.session import create_session_token, verify_session_token, revoke_session_token
from app.api.email_service import EmailService, get_email_service
from app.api.rate_limiter import check_rate_limit, get_client_ip
from app.api.security_utils import is_valid_token_format, get_token_fingerprint
from app.api.session import revoke_session_token, perform_logout
from app.api.web_views import spa_response
from app.database.notification_compatibility import repository_factory, repository_unit, account_request_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Preference Management"])

EMAIL_REGEX = re.compile(r"^[\w\.-]+@([\w\.-]+\.)+[\w-]{2,}$")

def get_db_repos():
    from app.database.repository import DB_PATH
    return repository_factory(DB_PATH)

def get_current_user_id(
    tpo_session: Optional[str] = Cookie(None),
    repos = Depends(get_db_repos)
) -> int:
    if not tpo_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session missing or expired. Please request a new preference access link."
        )
    user_id = verify_session_token(tpo_session, settings.SECRET_KEY)
    if not user_id:
        logger.warning("Session validation failed: token is expired, tampered, malformed, or revoked.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired or is invalid. Please request a new preference access link."
        )

    # Verify user exists in database and is active (revocation on unsubscribe)
    user_repo, _ = repos
    user = user_repo.get_by_id(user_id)
    from app.database.account_schema import account_policy_enabled
    from app.auth.accounts import AccountService
    production_access = None
    if hasattr(user_repo, "_get_conn"):
        with user_repo._get_conn() as connection:
            if account_policy_enabled(connection):
                production_access = AccountService(connection).session_valid(user_id, tpo_session)
    if not user or (production_access is False) or (production_access is None and user.get("is_active") != 1):
        logger.warning(f"Session rejected: user {user_id} not found or deactivated.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated or not found."
        )

    return user_id

class RequestLinkPayload(BaseModel):
    model_config = {"extra": "forbid"}

    email: str = Field(..., max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
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

@router.post("/preferences/confirm")
def exchange_magic_link_confirm(
    request: Request,
    response: Response,
    payload: ConfirmTokenPayload,
    repos = Depends(get_db_repos)
):
    """
    Explicit, user-initiated magic link confirmation endpoint.
    Exchanges a single-use action token for a secure 1-hour HttpOnly session cookie.
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
        key=f"exchange:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_EXCHANGE_PER_IP", 30),
        window_seconds=60
    )

    fingerprint = get_token_fingerprint(payload.token)
    user_repo, token_repo = repos

    with repository_unit(repos):
        from app.database.account_schema import account_policy_enabled
        from app.auth.accounts import AccountService
        production = False
        if hasattr(user_repo, "_get_conn"):
            with user_repo._get_conn() as connection:
                production = account_policy_enabled(connection)
        token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
        token_row = token_repo.claim_action_token(token_hash, "ACCOUNT_LOGIN" if production else "MANAGE_PREFS")

        if not token_row:
            logger.warning(f"Magic link confirm failed: invalid or expired token (fingerprint: {fingerprint}).")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired preference link."
            )

        user_id = token_row["user_id"]
        user = user_repo.get_by_id(user_id)
        allowed = bool(user and user.get("is_active") == 1)
        if production:
            with user_repo._get_conn() as connection:
                allowed = AccountService(connection).can_login(user_id)
        if not allowed:
            logger.warning(f"Session creation rejected: user {user_id} not found or deactivated.")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is deactivated or not found."
            )

        # Supersede/invalidate any older unused preference tokens for this user
        token_repo.invalidate_user_tokens(user_id, "ACCOUNT_LOGIN" if production else "MANAGE_PREFS")
        session_token = create_session_token(user_id=user_id, secret_key=settings.SECRET_KEY, max_age_seconds=3600)
        if production:
            with user_repo._get_conn() as connection:
                service = AccountService(connection)
                existing = request.cookies.get("tpo_session")
                if existing:
                    service.revoke_session(existing)
                service.register_session(user_id, session_token)

    # Session Fixation Prevention: revoke any pre-existing session token from the client
    existing_cookie = request.cookies.get("tpo_session")
    if existing_cookie:
        revoke_session_token(existing_cookie)

    # Create fresh 1-hour session token with 256-bit CSPRNG entropy

    is_secure = (
        bool(getattr(settings, "COOKIE_SECURE", True))
        or request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )

    response.set_cookie(
        key="tpo_session",
        value=session_token,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/"
    )

    logger.info(f"Magic link successfully exchanged for session: user {user_id} (fingerprint: {fingerprint}).")

    return {
        "status": "success",
        "message": "Preferences session authenticated."
    }


@router.get("/preferences/request")
def exchange_magic_link_legacy(
    request: Request,
    token: str = Query(..., description="15-minute single-use action token")
):
    """
    Legacy preferences magic-link redirector.
    Harmless, non-mutating 303 redirect to the SPA preferences confirmation view with URL fragment.
    Does NOT validate, consume, or establish a session.
    """
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"exchange:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_EXCHANGE_PER_IP", 30),
        window_seconds=60
    )

    return RedirectResponse(
        url=f"/preferences/confirm#token={token}",
        status_code=status.HTTP_303_SEE_OTHER
    )

@router.post("/preferences/request-link")
def request_preference_link(
    request: Request,
    payload: RequestLinkPayload,
    repos = Depends(get_db_repos),
    email_service: EmailService = Depends(get_email_service)
):
    """
    Requests a fresh 15-minute access link sent directly to the student's email.
    Includes rate limiting and uniform non-enumerating responses.
    """
    # 0. Enforce rate limiting
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"req_link:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_MAGIC_LINK_PER_IP", 30),
        window_seconds=60
    )
    check_rate_limit(
        request=request,
        key=f"req_link:email:{hashlib.sha256(payload.email.lower().encode('utf-8')).hexdigest()}",
        max_requests=getattr(settings, "RATE_LIMIT_MAGIC_LINK_PER_EMAIL", 5),
        window_seconds=900,
        detail="Too many preference link requests for this email address. Please try again later."
    )

    user_repo, token_repo = repos
    raw_token = None
    with repository_unit(repos):
        user = user_repo.get_by_email(payload.email)
        production = False
        allowed = bool(user and user["is_verified"] == 1 and user["is_active"] == 1)
        from app.database.account_schema import account_policy_enabled
        from app.auth.accounts import AccountService
        if hasattr(user_repo, "_get_conn"):
            with user_repo._get_conn() as connection:
                production = account_policy_enabled(connection)
                if production:
                    allowed = bool(user and AccountService(connection).can_login(user["id"]))
        if allowed:
            kind = "ACCOUNT_LOGIN" if production else "MANAGE_PREFS"
            token_repo.invalidate_user_tokens(user["id"], kind)
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            token_repo.create_token(user["id"], token_hash, kind,
                datetime.now(timezone.utc) + timedelta(minutes=15))
    if raw_token is not None:
        try:
            email_service.send_preference_link_email(user["email"], raw_token)
        except Exception:
            logger.warning("Account-link request could not complete transport processing.")
    return account_request_response(repos,
        "If an active account exists for this email, a 15-minute access link has been sent to your inbox.")

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
    request: Request,
    payload: PreferenceUpdate,
    user_id: int = Depends(get_current_user_id),
    repos = Depends(get_db_repos)
):
    """
    Updates the preferences for the authenticated session user.
    """
    client_ip = get_client_ip(request)
    check_rate_limit(
        request=request,
        key=f"pref_update:ip:{client_ip}",
        max_requests=getattr(settings, "RATE_LIMIT_PREF_UPDATE_PER_IP", 30),
        window_seconds=60
    )

    user_repo, _ = repos
    user_repo.update_preferences(
        user_id=user_id,
        pref_internship=payload.pref_internship,
        pref_placement=payload.pref_placement,
        pref_ppo=payload.pref_ppo
    )
    logger.info(f"Preferences updated successfully for user {user_id}.")
    return {
        "status": "success",
        "message": "Preferences updated successfully."
    }

@router.post("/preferences/logout")
def logout_preferences(
    response: Response,
    request: Request,
    repos = Depends(get_db_repos)
):
    """
    Temporary compatibility route for session logout.
    Delegates to shared perform_logout handler.
    """
    return perform_logout(request, response, repos=repos)
