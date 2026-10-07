"""REST endpoints for admin authentication (login + API-only password reset).

The UI only logs in and out; password changes are deliberately API-only and
require BOTH a valid non-expired bearer token AND the ``PASSWORD_RESET_SECRET``
that lives in the gitignored .env.
"""

import hmac
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import get_settings
from app.models import (
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetRequest,
    PasswordResetResponse,
)

if TYPE_CHECKING:
    from app.auth_store import AuthStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_store(request: Request) -> "AuthStore":
    return request.app.state.auth_store  # type: ignore[attr-defined]


# ------------------------------------------------------------------
# Login
# ------------------------------------------------------------------

@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}},
)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    """Exchange credentials for a bearer token (7-day expiry by default)."""
    stored_hash = _get_store(request).get_password_hash(body.username)
    if stored_hash is None or not verify_password(body.password, stored_hash):
        # Same detail for unknown user and wrong password — no user oracle.
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token, expires_at = create_access_token(body.username)
    logger.info("Login OK for %s", body.username)
    return LoginResponse(token=token, expires_at=expires_at)


# ------------------------------------------------------------------
# Password reset (API-only; UI has no reset flow)
# ------------------------------------------------------------------

@router.post(
    "/reset-password",
    response_model=PasswordResetResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def reset_password(
    request: Request,
    body: PasswordResetRequest,
    _user: str = Depends(get_current_user),
) -> PasswordResetResponse:
    """Change an admin password — bearer token AND the reset secret required."""
    # Compare bytes: hmac.compare_digest(str, str) rejects non-ASCII input.
    if not hmac.compare_digest(
        body.secret.encode("utf-8"),
        get_settings().password_reset_secret.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Invalid reset secret")
    store = _get_store(request)
    if store.get_password_hash(body.username) is None:
        raise HTTPException(status_code=404, detail=f"User {body.username} not found")
    store.upsert_user(body.username, hash_password(body.new_password))
    logger.info("Password reset for %s (by %s)", body.username, _user)
    return PasswordResetResponse()
