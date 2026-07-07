import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt, JWTError
from backend.utils.config import settings


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(username: str) -> str:
    """Create JWT access token with username as subject."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.admin_token_expire_minutes)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.admin_session_secret, algorithm="HS256")


def decode_access_token(token: str) -> Optional[str]:
    """Decode JWT token and return username, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.admin_session_secret, algorithms=["HS256"])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_admin(authorization_header: str) -> Optional[str]:
    """FastAPI dependency to extract and verify admin username from Authorization header."""
    try:
        scheme, token = authorization_header.split()
        if scheme.lower() != "bearer":
            return None
        return decode_access_token(token)
    except (ValueError, AttributeError):
        return None
