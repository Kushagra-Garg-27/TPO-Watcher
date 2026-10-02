import re
import hashlib
from typing import Optional

# Action tokens are typically 32 bytes URL-safe base64 (approx 43-44 chars).
# We accept safe alphanumeric strings with hyphens and underscores between 8 and 128 characters
# to support production CSPRNG tokens and test action tokens safely without risk of injection.
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

def is_valid_token_format(token: Optional[str]) -> bool:
    """
    Validates that a token has an acceptable length and consists strictly
    of URL-safe characters, preventing SQL injection, XSS, or oversized inputs.
    """
    if not token or not isinstance(token, str):
        return False
    stripped = token.strip()
    return bool(TOKEN_PATTERN.match(stripped))

def get_token_fingerprint(token: str) -> str:
    """
    Returns an irreversible 8-character hex fingerprint of the token's SHA-256 hash.
    Safe for security logging without exposing raw tokens.
    """
    if not token:
        return "empty"
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:8]
