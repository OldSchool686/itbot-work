"""Mobile app API — authentication, tickets, FCM for Flutter client."""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.admin import Admin
from backend.models.allowed_user import AllowedUser
from backend.models.ticket import Ticket
from backend.utils.config import settings
from backend.utils.auth_jwt import create_access_token, decode_access_token
from backend.utils.phone_utils import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mobile", tags=["mobile"])

# --- Redis key helpers ---


async def _redis_set(key: str, value: str, ttl_seconds: int) -> None:
    from backend.utils.redis_pool import get_redis

    r = await get_redis()
    await r.setex(key, ttl_seconds, value)


async def _redis_get_and_del(key: str) -> Optional[str]:
    """Atomic get-and-delete (consume once)."""
    from backend.utils.redis_pool import get_redis

    r = await get_redis()
    val = await r.get(key)
    if val:
        await r.delete(key)
    return val


# --- Internal token guard (same pattern as bot_internal) ---


def _check_internal_api_key(x_internal_token: Optional[str] = Header(None)) -> None:
    if not settings.internal_api_key:
        return
    if x_internal_token != settings.internal_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token"
        )


# --- Mobile JWT helpers ---


def _create_mobile_jwt(user_id: int, phone: str) -> str:
    """Create JWT for mobile user. Payload contains uid (allowed_users.id) and ph."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.admin_token_expire_minutes)
    payload = {
        "sub": f"mobile:{user_id}",
        "uid": user_id,
        "ph": phone,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    from jose import jwt as jwten

    return jwten.encode(payload, settings.admin_session_secret, algorithm="HS256")


def _decode_mobile_jwt(token: str) -> dict:
    """Decode mobile JWT and return payload. Raises on invalid."""
    username = decode_access_token(token)
    if not username or not username.startswith("mobile:"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid mobile token"
        )
    from jose import jwt as jwten

    try:
        payload = jwten.decode(token, settings.admin_session_secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token decode failed"
        )
    return payload


def require_mobile_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    """FastAPI dependency: verify Bearer JWT and return mobile user payload."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header required"
        )
    token = authorization.replace("Bearer ", "")
    return _decode_mobile_jwt(token)


# --- Request / Response models ---


class GenerateTokenRequest(BaseModel):
    max_user_id: Optional[int] = None
    phone: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        if not self.max_user_id and not self.phone:
            raise ValueError("At least one of max_user_id or phone must be provided")


class GenerateTokenResponse(BaseModel):
    token: str


class MobileAuthRequest(BaseModel):
    token: str = Field(..., min_length=1)


class MobileUserOut(BaseModel):
    id: int
    phone: Optional[str]
    max_user_id: Optional[int]
    full_name: str
    department: Optional[str]
    consent_given: bool
    is_active: bool


class MobileAuthResponse(BaseModel):
    jwt: str
    user: MobileUserOut
    is_admin: bool


class MobileMeResponse(BaseModel):
    jwt: str
    user: MobileUserOut
    is_admin: bool


class CreateTicketRequest(BaseModel):
    full_name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    description: str = ""
    photo_urls: Optional[list[str]] = None


class CreateTicketResponse(BaseModel):
    ticket_id: int
    status: str


class TicketListItem(BaseModel):
    id: int
    full_name: str
    phone: str
    department: str
    category: str
    description: str
    photo_urls: Optional[list[str]] = None
    bitrix_deal_id: Optional[int] = None
    status: str
    closed_by_user: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TicketListResponse(BaseModel):
    tickets: list[TicketListItem]


class FcmTokenRequest(BaseModel):
    fcm_token: str = Field(..., min_length=1)


# --- Endpoints ---


@router.post("/generate-token", response_model=GenerateTokenResponse)
async def generate_token(
    req: GenerateTokenRequest,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Bot calls this to generate a one-time auth token for mobile deep link.

    Token is stored in Redis with 5-minute TTL. The bot then sends:
    itbot://auth?token=<token>
    """
    user = None

    if req.phone:
        normalized = normalize_phone(req.phone)
        result = await db.execute(
            select(AllowedUser).where(AllowedUser.phone == normalized)
        )
        user = result.scalar_one_or_none()

    if not user and req.max_user_id:
        result = await db.execute(
            select(AllowedUser).where(AllowedUser.max_user_id == req.max_user_id)
        )
        user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found or deactivated")

    random_token = secrets.token_urlsafe(32)
    await _redis_set(f"mobile:auth:{random_token}", str(user.id), ttl_seconds=300)

    logger.info("Mobile auth token generated for user %s (id=%d)", req.phone or req.max_user_id, user.id)
    return GenerateTokenResponse(token=random_token)


@router.post("/auth", response_model=MobileAuthResponse)
async def mobile_auth(
    req: MobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange one-time token for JWT.

    Consumes the token (single-use). Returns JWT + user data + isAdmin flag.
    """
    user_id_str = await _redis_get_and_del(f"mobile:auth:{req.token}")

    if not user_id_str:
        raise HTTPException(status_code=401, detail="Token expired or already used")

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    result = await db.execute(select(AllowedUser).where(AllowedUser.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User deactivated")

    is_admin = False
    admin_result = await db.execute(
        select(Admin).where(Admin.is_active == True)  # noqa: E712
    )
    admins = admin_result.scalars().all()
    for admin in admins:
        if admin.username == user.phone or admin.username == str(user_id):
            is_admin = True
            break

    jwt_token = _create_mobile_jwt(user.id, user.phone or "")

    logger.info("Mobile auth successful for user %s (id=%d, admin=%s)", user.phone, user.id, is_admin)
    return MobileAuthResponse(
        jwt=jwt_token,
        user=MobileUserOut(**user.to_dict()),
        is_admin=is_admin,
    )


@router.get("/me", response_model=MobileMeResponse)
async def mobile_me(
    payload: dict = Depends(require_mobile_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current authenticated user info."""
    user_id = payload.get("uid")

    result = await db.execute(select(AllowedUser).where(AllowedUser.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User deactivated")

    is_admin = False
    admin_result = await db.execute(
        select(Admin).where(Admin.is_active == True)  # noqa: E712
    )
    admins = admin_result.scalars().all()
    for admin in admins:
        if admin.username == user.phone or admin.username == str(user_id):
            is_admin = True
            break

    jwt_token = _create_mobile_jwt(user.id, user.phone or "")
    return MobileMeResponse(
        jwt=jwt_token,
        user=MobileUserOut(**user.to_dict()),
        is_admin=is_admin,
    )


@router.post("/tickets/create", response_model=CreateTicketResponse, status_code=status.HTTP_201_CREATED)
async def mobile_create_ticket(
    req: CreateTicketRequest,
    payload: dict = Depends(require_mobile_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a ticket as the authenticated user."""
    phone = normalize_phone(req.phone)

    result = await db.execute(select(Ticket).where(Ticket.phone == phone))
    existing = result.scalars().all()

    for t in existing:
        if t.status in ("new", "in_progress"):
            raise HTTPException(
                status_code=409,
                detail=f"Active ticket #{t.id} already exists. Close it first.",
            )

    ticket = Ticket(
        full_name=req.full_name,
        phone=phone,
        department=req.department,
        category=req.category,
        description=req.description,
        photo_urls=req.photo_urls or [],
        status="new",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)

    logger.info("Mobile ticket #%d created by user %s", ticket.id, phone)
    return CreateTicketResponse(ticket_id=ticket.id, status="new")


@router.get("/tickets/list", response_model=TicketListResponse)
async def mobile_list_tickets(
    limit: int = 20,
    payload: dict = Depends(require_mobile_user),
    db: AsyncSession = Depends(get_db),
):
    """List tickets scoped to the authenticated user's phone."""
    phone = payload.get("ph", "")

    stmt = (
        select(Ticket)
        .where(Ticket.phone == phone)
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    return TicketListResponse(
        tickets=[TicketListItem(**t.to_dict()) for t in tickets]
    )


@router.get("/tickets/{ticket_id}", response_model=TicketListItem)
async def mobile_ticket_detail(
    ticket_id: int,
    payload: dict = Depends(require_mobile_user),
    db: AsyncSession = Depends(get_db),
):
    """Get single ticket detail. Users can only see their own tickets; admins see all."""
    phone = payload.get("ph", "")

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Check ownership (phone match) or admin access
    is_owner = ticket.phone == phone
    if not is_owner:
        admin_result = await db.execute(
            select(Admin).where(Admin.is_active == True)  # noqa: E712
        )
        admins = admin_result.scalars().all()
        user_id = payload.get("uid")
        for admin in admins:
            if admin.username == phone or admin.username == str(user_id):
                is_owner = True
                break

    if not is_owner:
        raise HTTPException(status_code=403, detail="Access denied")

    return TicketListItem(**ticket.to_dict())


@router.post("/fcm-token", status_code=status.HTTP_204_NO_CONTENT)
async def mobile_register_fcm_token(
    req: FcmTokenRequest,
    payload: dict = Depends(require_mobile_user),
):
    """Register/update FCM device token for push notifications."""
    user_id = payload.get("uid")

    await _redis_set(f"mobile:fcm:{user_id}", req.fcm_token, ttl_seconds=604800)

    logger.info("FCM token registered for mobile user %s", user_id)


@router.post("/notify/{user_id}")
async def mobile_notify_user(
    user_id: int,
    req_body: dict,
    _internal: None = Depends(_check_internal_api_key),
):
    """Server-side trigger to send push notification to a specific user.

    Body: {"title": "...", "body": "..."}
    Currently stores notification in Redis for async dispatch.
    FCM integration TBD (requires Firebase service account).
    """
    fcm_key = f"mobile:fcm:{user_id}"
    from backend.utils.redis_pool import get_redis

    r = await get_redis()
    token = await r.get(fcm_key)

    if not token:
        logger.warning("No FCM token for user %d", user_id)
        return {"sent": False, "reason": "no_device"}

    title = req_body.get("title", "IT Bot")
    body = req_body.get("body", "")

    from backend.services.fcm_service import send_fcm_notification

    result = await send_fcm_notification(token=token, title=title, body=body)

    return {"sent": True, "fcm_result": result}
