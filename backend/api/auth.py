import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.admin import Admin
from backend.utils.auth_jwt import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.utils.config import settings
from backend.utils.brute_force_protection import get_brute_force_protector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _get_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For (nginx proxy) or direct connection."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    protector = get_brute_force_protector()
    ip = _get_client_ip(request)

    # 1) Check if account is currently locked out
    if await protector.is_account_locked(req.username):
        remaining = await protector.get_lockout_remaining(req.username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Account temporarily locked. "
                f"Try again in {remaining // 60} minutes."
            ),
        )

    try:
        result = await db.execute(select(Admin).where(Admin.username == req.username))
        admin = result.scalar_one_or_none()

        # 2) Constant-time check: always call verify_password to prevent timing attack.
        #    If user doesn't exist, compare against a dummy hash so bcrypt still runs.
        valid = False
        if admin and admin.password_hash:
            try:
                valid = verify_password(req.password, admin.password_hash)
            except Exception as e:
                logger.error(f"Password verification error for {admin.username}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal server error",
                )
        else:
            # Dummy bcrypt hash — forces same ~300ms delay as real verify.
            dummy_hash = (
                "$2b$12$LJ3m4vYl8e8rR6p5Q9xOy.Kz7fN2wH1cTgBdAeSfGhIjKlMnOpQr"
            )
            try:
                verify_password(req.password, dummy_hash)
            except Exception:
                pass

        # 3) Failed authentication — record and possibly block
        if not valid:
            bf_result = await protector.record_failed_attempt(ip, req.username)
            if bf_result["blocked"]:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=bf_result["reason"],
                )
            # Generic error — doesn't reveal whether the username exists
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        # 4) Account disabled by admin?
        if not admin.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled",
            )

        # 5) Success — reset brute-force counters + progressive IP lockout counter
        await protector.record_success(req.username)
        await protector.reset_ip_lockout_count(ip)

        admin.last_login_at = datetime.now(timezone.utc)
        await db.commit()

        token = create_access_token(admin.username)

        resp = JSONResponse({
            "access_token": token,
            "token_type": "bearer",
        })
        resp.set_cookie(
            key="admin_token",
            value=token,
            max_age=settings.admin_token_expire_minutes * 60,
            httponly=True,
            secure=False,  # Set True for HTTPS production
            samesite="lax",
            path="/",
        )
        logger.info(f"User {admin.username} logged in successfully from {ip}")
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Login failed with unexpected error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login error",
        )


@router.post("/logout")
async def logout():
    """Clear auth cookie and respond."""
    resp = JSONResponse({"message": "logged out"})
    resp.delete_cookie(key="admin_token", path="/")
    return resp


@router.get("/me")
async def me(request: Request, authorization: Optional[str] = Header(None)):
    token = None
    if authorization:
        try:
            scheme, t = authorization.split()
            if scheme.lower() == "bearer":
                token = t
        except (ValueError, AttributeError):
            pass
    if not token:
        token = request.cookies.get("admin_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )

    try:
        username = decode_access_token(token)
    except Exception:
        pass

    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return {"username": username}


__all__ = ["router", "hash_password"]
