import hmac
import hashlib
import time
from typing import Optional

def create_session_token(user_id: int, secret_key: str, max_age_seconds: int = 3600) -> str:
    """
    Creates an HMAC-SHA256 signed session token containing user_id and expiry.
    """
    expires_at = int(time.time()) + max_age_seconds
    payload = f"{user_id}:{expires_at}"
    sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_session_token(token: str, secret_key: str) -> Optional[int]:
    """
    Verifies an HMAC-SHA256 signed session token.
    Returns user_id if valid and not expired, else None.
    """
    if not token or ":" not in token:
        return None

    parts = token.split(":")
    if len(parts) != 3:
        return None

    uid_str, exp_str, sig = parts
    try:
        user_id = int(uid_str)
        expires_at = int(exp_str)
    except ValueError:
        return None

    # Check expiry
    if time.time() > expires_at:
        return None

    # Verify signature
    payload = f"{user_id}:{expires_at}"
    expected_sig = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    return user_id
