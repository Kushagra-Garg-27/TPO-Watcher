import hmac
import hashlib
import time
import secrets
import logging
import threading
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Server-side revocation store for invalidated sessions: mapping sha256(token) -> expires_at
_REVOKED_SESSIONS: Dict[str, int] = {}
_REVOCATION_LOCK = threading.Lock()

def create_session_token(user_id: int, secret_key: str, max_age_seconds: int = 3600) -> str:
    """
    Creates an HMAC-SHA256 signed session token containing:
        user_id : expires_at : nonce : sig
    Includes 256 bits of CSPRNG entropy in the nonce to prevent session prediction,
    replay collisions, and deterministic token generation.
    """
    expires_at = int(time.time()) + max_age_seconds
    nonce = secrets.token_hex(32)  # 256 bits of CSPRNG randomness (64 hex characters)
    payload = f"{user_id}:{expires_at}:{nonce}"
    sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_session_token(token: str, secret_key: str) -> Optional[int]:
    """
    Verifies an HMAC-SHA256 signed session token.
    Strictly requires the 4-part high-entropy token format: user_id:expires_at:nonce:sig.
    Legacy 3-part tokens lacking CSPRNG nonces are strictly rejected to eliminate
    replay downgrade risks.
    Returns user_id if valid, not expired, and not revoked, else None.
    """
    if not token or ":" not in token:
        return None

    # Length guard to prevent oversized payload abuse
    if len(token) > 512:
        return None

    parts = token.split(":")
    # Strictly enforce 4 parts: user_id : expires_at : nonce : sig
    if len(parts) != 4:
        return None

    uid_str, exp_str, nonce, sig = parts

    # Validate nonce and signature lengths (both must be 64-char hex strings)
    if len(nonce) != 64 or len(sig) != 64:
        return None

    try:
        user_id = int(uid_str)
        expires_at = int(exp_str)
        # Verify nonce and sig are valid hexadecimal
        int(nonce, 16)
        int(sig, 16)
    except ValueError:
        return None

    # Check expiry
    now = int(time.time())
    if now > expires_at:
        return None

    # Verify signature in constant time
    payload = f"{uid_str}:{exp_str}:{nonce}"
    expected_sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Check server-side revocation
    token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _REVOCATION_LOCK:
        if token_fingerprint in _REVOKED_SESSIONS:
            exp_revoked = _REVOKED_SESSIONS[token_fingerprint]
            if now > exp_revoked:
                # Expired naturally; clean up to bound memory usage
                del _REVOKED_SESSIONS[token_fingerprint]
            else:
                return None

    return user_id

def revoke_session_token(token: str) -> None:
    """
    Explicitly revokes a session token server-side so it cannot be reused.
    Stores the SHA-256 fingerprint of the token with its expiration timestamp.
    """
    if not token or ":" not in token:
        return
    token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    parts = token.split(":")
    expires_at = int(time.time()) + 3600
    if len(parts) >= 2:
        try:
            expires_at = int(parts[1])
        except ValueError:
            pass

    with _REVOCATION_LOCK:
        _REVOKED_SESSIONS[token_fingerprint] = expires_at

def reset_session_revocations() -> None:
    """Clears all session revocations. Primarily used for testing."""
    with _REVOCATION_LOCK:
        _REVOKED_SESSIONS.clear()


def perform_logout(request, response) -> dict:
    """
    Consolidated session logout handler:
    1. Revokes server-side session token if present.
    2. Clears client-side session cookie with matching security attributes.
    """
    from app.config import settings

    tpo_session = request.cookies.get("tpo_session")
    if tpo_session:
        revoke_session_token(tpo_session)
        logger.info("Session revoked upon logout.")
    is_secure = bool(
        settings.COOKIE_SECURE
        or request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    response.delete_cookie(
        key="tpo_session",
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_secure
    )
    return {"status": "success", "message": "Successfully logged out."}

