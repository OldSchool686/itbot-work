from typing import Optional

from fastapi import Header, HTTPException, Request, status


def _extract_token(authorization: Optional[str], request: Request = None) -> str:
    """Extract JWT from Bearer header or HttpOnly cookie."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.replace("Bearer ", "")
    if request and request.cookies.get("admin_token"):
        return request.cookies["admin_token"]
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def require_admin(
    authorization: Optional[str] = Header(None),
    request: Request = None  # injected by FastAPI
) -> str:
    """FastAPI dependency that verifies admin JWT and returns username for audit logging."""
    from backend.utils.auth_jwt import decode_access_token

    token = _extract_token(authorization, request)
    username = decode_access_token(token)

    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return username


def require_admin_void(
    authorization: Optional[str] = Header(None),
    request: Request = None  # injected by FastAPI
) -> None:
    """FastAPI dependency that verifies admin JWT (void return for backward compatibility)."""
    from backend.utils.auth_jwt import decode_access_token

    token = _extract_token(authorization, request)
    username = decode_access_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
