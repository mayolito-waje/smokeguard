"""Admin authentication: bcrypt password hashing and JWT bearer tokens.

The PoC has a single admin user (bootstrapped in ``main.py`` when the auth
store is empty).  Two independent secrets live in the gitignored .env:
``JWT_SECRET`` signs session tokens (7-day expiry by default) and
``PASSWORD_RESET_SECRET`` is the second factor for the API-only password
reset.  Tokens are stateless — logout and password resets do not revoke
them; they expire on their own.
"""

import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_ALGORITHM = "HS256"

# auto_error=False lets us control the 401 detail (missing vs invalid/expired
# token); FastAPI's auto-error would raise 401 + WWW-Authenticate as well.
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing (bcrypt, salted per hash)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a plaintext password with a fresh bcrypt salt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # bcrypt 5.x raises (>72-byte input) rather than truncating — treat
        # an oversized password as a failed login, not a 500.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(username: str) -> tuple[str, float]:
    """Issue an HS256 bearer token; returns (token, expires_at epoch seconds)."""
    settings = get_settings()
    now = time.time()
    expires_at = now + settings.jwt_expire_days * 86400
    token = jwt.encode(
        {"sub": username, "iat": int(now), "exp": int(expires_at)},
        settings.jwt_secret,
        algorithm=_ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """Decode and validate a bearer token (raises ``jwt.InvalidTokenError``)."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Reusable route dependency: valid non-expired bearer token → username."""
    if credentials is None:
        raise _unauthorized("Missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:  # parent of ExpiredSignatureError
        raise _unauthorized("Invalid or expired token")
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        raise _unauthorized("Invalid token payload")
    return username
