import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from datetime import datetime, timedelta, timezone

from backend.models.allowed_user import AllowedUser
from backend.models.ticket import Ticket
from backend.models.user import User
from backend.utils.config import settings
from backend.utils.phone_utils import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bot", tags=["bot internal"])


def _check_internal_api_key(x_internal_token: Optional[str] = Header(None)) -> None:
    """Verify the X-Internal-Token header matches configured API key."""
    if not settings.internal_api_key:
        return  # Disabled when no key is set (development mode)
    if x_internal_token != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


async def _check_rate_limit(request: Request):
    """Rate limit internal API requests by client IP."""
    from backend.utils.rate_limiter import check_rate_limit

    ip = request.client.host if request.client else "unknown"
    await check_rate_limit(request, f"bot_api:{ip}")


# --- Request/Response models ---


class CheckAccessRequest(BaseModel):
    phone: Optional[str] = None
    max_user_id: Optional[int] = None

    @model_validator(mode='after')
    def validate_fields(self):
        if not self.phone and not self.max_user_id:
            raise ValueError('At least one of phone or max_user_id must be provided')
        return self


class CheckAccessResponse(BaseModel):
    allowed: bool
    reason: str = ""
    user_data: Optional[dict] = None
    consent_given: bool = False


class UserByPhoneResponse(BaseModel):
    full_name: Optional[str] = None
    department: Optional[str] = None
    consent_given: bool = False


class TicketStatusResponse(BaseModel):
    status: str
    bitrix_deal_id: Optional[int] = None
    updated_at: Optional[str] = None


class UserTicketsResponse(BaseModel):
    tickets: list[dict]


class NotifyUserRequest(BaseModel):
    ticket_id: int
    message: str = Field(..., min_length=1)


# --- Helpers ---


# --- Endpoints ---


@router.post("/check-access", response_model=CheckAccessResponse)
async def check_access(
    req: CheckAccessRequest,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Check if a user is allowed to use the bot.

    Supports lookup by phone number or MAX user ID.
    Called by the bot service before allowing any interaction.
    Protected by X-Internal-Token header.
    """
    user = None

    # Try phone lookup first
    if req.phone:
        normalized = normalize_phone(req.phone)
        result = await db.execute(select(AllowedUser).where(AllowedUser.phone == normalized))
        user = result.scalar_one_or_none()

    # Fallback to max_user_id lookup
    if not user and req.max_user_id:
        result = await db.execute(select(AllowedUser).where(AllowedUser.max_user_id == req.max_user_id))
        user = result.scalar_one_or_none()

    if user and user.is_active:
        return CheckAccessResponse(
            allowed=True,
            reason="",
            user_data={
                "id": user.id,
                "phone": user.phone,
                "full_name": user.full_name,
                "department": user.department,
            },
            consent_given=user.consent_given,
        )

    if user and not user.is_active:
        return CheckAccessResponse(allowed=False, reason="deactivated")

    return CheckAccessResponse(allowed=False, reason="not_found")


@router.get("/user-by-phone", response_model=UserByPhoneResponse)
async def user_by_phone(
    phone: str = Query(..., min_length=1),
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Look up a user by phone number.

    Returns empty dict fields if user not found — never 404s for missing users.
    No authentication required — internal service-to-service endpoint.
    """
    normalized = normalize_phone(phone)
    result = await db.execute(select(AllowedUser).where(AllowedUser.phone == normalized))
    user = result.scalar_one_or_none()

    if user:
        return UserByPhoneResponse(
            full_name=user.full_name,
            department=user.department,
            consent_given=user.consent_given,
        )

    return UserByPhoneResponse()


@router.get("/ticket-status/{ticket_id}", response_model=TicketStatusResponse)
async def get_ticket_status(
    ticket_id: int,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Get current status of a support ticket.

    Phase 5 placeholder — returns basic status info.
    No authentication required — internal service-to-service endpoint.
    """
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    return TicketStatusResponse(
        status=ticket.status,
        bitrix_deal_id=ticket.bitrix_deal_id,
        updated_at=ticket.updated_at.isoformat() if ticket.updated_at else None,
    )


@router.get("/user-tickets", response_model=UserTicketsResponse)
async def get_user_tickets(
    phone: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Get recent tickets for a user by phone number."""
    normalized = normalize_phone(phone)
    result = await db.execute(
        select(Ticket)
        .where(Ticket.phone == normalized)
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    )
    tickets = result.scalars().all()
    return UserTicketsResponse(
        tickets=[
            {
                "id": t.id,
                "category": t.category,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ]
    )


class CloseTicketRequest(BaseModel):
    ticket_id: int
    phone: str = Field(..., min_length=1)


class CloseTicketResponse(BaseModel):
    success: bool
    message: str


@router.post("/close-ticket", response_model=CloseTicketResponse)
async def close_ticket(
    req: CloseTicketRequest,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    normalized = normalize_phone(req.phone)
    result = await db.execute(
        select(Ticket).where((Ticket.id == req.ticket_id) & (Ticket.phone == normalized))
    )
    ticket = result.scalar_one_or_none()

    if not ticket:
        return CloseTicketResponse(success=False, message="Заявка не найдена или недоступна")

    if ticket.status == "closed":
        return CloseTicketResponse(success=True, message="Заявка уже закрыта")

    old_status = ticket.status
    ticket.status = "closed"
    ticket.closed_by_user = True
    await db.commit()
    logger.info(f"Ticket #{req.ticket_id} closed by user phone={normalized} (was: {old_status})")

    return CloseTicketResponse(
        success=True,
        message=f"Заявка #{req.ticket_id} (была: {old_status}) закрыта пользователем",
    )


@router.post("/notify-user")
async def notify_user(
    req: NotifyUserRequest,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Send a notification message to the MAX user who owns a ticket.

    Looks up max_user_id from phone → calls bot /bot/send-message endpoint.
    No authentication required — internal service-to-service endpoint.
    """
    result = await db.execute(select(Ticket).where(Ticket.id == req.ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    # Look up max_user_id from allowed_users or users table by phone
    normalized = normalize_phone(ticket.phone)
    user_id: int | None = None

    result = await db.execute(select(AllowedUser.max_user_id).where((AllowedUser.phone == normalized) & (AllowedUser.max_user_id.isnot(None))))
    row = result.one_or_none()
    if row:
        user_id = row[0]

    if not user_id:
        result = await db.execute(select(User.max_user_id).where((User.phone == normalized) & (User.max_user_id.isnot(None))))
        row = result.one_or_none()
        if row:
            user_id = row[0]

    if not user_id:
        logger.warning(f"Cannot notify ticket #{req.ticket_id}: no max_user_id for phone={normalized}")
        return {
            "sent": False,
            "ticket_id": ticket.id,
            "reason": "max_user_id not found",
        }

    # Call bot /bot/send-message endpoint
    import httpx

    message_text = (
        f"🔔 Ответ администратора по заявке #{req.ticket_id}\n\n"
        f"{req.message}\n\n"
        f"📢 УВЕДОМЛЕНИЕ: Пожалуйста, не отвечайте на сообщение, оно не будет доставлено."
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.bot_webhook_url}/bot/send-message",
                data={"user_id": str(user_id), "text": message_text},
                headers={"X-Internal-Token": settings.internal_api_key or ""},
            )

        if resp.status_code == 200:
            logger.info(f"Notification sent for ticket #{req.ticket_id} to user_id={user_id}")
            return {"sent": True, "ticket_id": ticket.id, "user_id": user_id}
        else:
            logger.warning(f"Bot send failed ({resp.status_code}): {resp.text[:200]}")
            return {"sent": False, "ticket_id": ticket.id, "reason": f"bot error {resp.status_code}"}

    except httpx.RequestError as e:
        logger.error(f"Bot unreachable when notifying for ticket #{req.ticket_id}: {e}")
        return {"sent": False, "ticket_id": ticket.id, "reason": str(e)}


class SaveUserRequest(BaseModel):
    max_user_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None


class SaveUserResponse(BaseModel):
    is_new: bool
    user_data: dict


class ConsentGiveRequest(BaseModel):
    phone: str = Field(..., min_length=1)


class LinkUserRequest(BaseModel):
    """Link a MAX user ID to an allowed_users record."""
    phone: str = Field(..., min_length=1)
    max_user_id: int


@router.post("/link-user")
async def link_user(
    req: LinkUserRequest,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Link a MAX user ID to an existing allowed_users record by phone.

    Called when user shares their contact for the first time.
    Updates max_user_id on the allowed_users row.
    """
    normalized = normalize_phone(req.phone)
    result = await db.execute(select(AllowedUser).where(AllowedUser.phone == normalized))
    user = result.scalar_one_or_none()

    if user:
        user.max_user_id = req.max_user_id
        await db.commit()

    return {"linked": True, "phone": normalized, "max_user_id": req.max_user_id}


@router.post("/save-user", response_model=SaveUserResponse)
async def save_user(
    req: SaveUserRequest,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Save or update a MAX user profile record in the users table.

    Called when bot_started event fires (user clicks 'Start' button).
    Returns is_new flag to distinguish first launch from returning users.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(User).where(User.max_user_id == req.max_user_id)
    )
    user = result.scalar_one_or_none()
    is_new = False

    if user:
        # Update existing record
        user.first_name = req.first_name or user.first_name
        user.last_name = req.last_name or user.last_name
        user.updated_at = now
        if req.phone:
            user.phone = normalize_phone(req.phone)
    else:
        is_new = True
        user = User(
            max_user_id=req.max_user_id,
            first_name=req.first_name,
            last_name=req.last_name,
            phone=normalize_phone(req.phone) if req.phone else None,
            created_at=now,
            updated_at=now,
        )
        db.add(user)

    await db.commit()
    return SaveUserResponse(is_new=is_new, user_data=user.to_dict())


@router.post("/consent-give")
async def consent_give(
    req: ConsentGiveRequest,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Mark personal data processing consent as given for a user.

    Updates both allowed_users and users tables.
    No authentication required — internal service-to-service endpoint.
    """
    normalized = normalize_phone(req.phone)
    now = datetime.now(timezone.utc)

    # Update allowed_users.consent_given
    result = await db.execute(select(AllowedUser).where(AllowedUser.phone == normalized))
    allowed_user = result.scalar_one_or_none()
    if allowed_user:
        allowed_user.consent_given = True
        allowed_user.consent_timestamp = now

    # Update users.consent_given (if runtime user record exists)
    users_result = await db.execute(select(User).where(User.phone == normalized))
    user = users_result.scalar_one_or_none()
    if user:
        user.consent_given = True
        user.consent_timestamp = now

    await db.commit()
    return {"consent_given": True, "phone": normalized}


@router.get("/ticket-position")
async def get_ticket_position(
    ticket_id: int = Query(...),
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
    db: AsyncSession = Depends(get_db),
):
    """Get queue position for a ticket.

    Counts open tickets (new/in_progress) created within 7 days before this ticket.
    Used to show «Ожидайте обработки, перед вами N заявок» message.
    """
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )

    cutoff = ticket.created_at - timedelta(days=7)
    count_result = await db.execute(
        select(func.count(Ticket.id)).where(
            (Ticket.created_at >= cutoff) &
            (Ticket.created_at < ticket.created_at) &
            (Ticket.status.in_(["new", "in_progress"]))
        )
    )
    position = count_result.scalar() or 0

    return {"ticket_id": ticket_id, "position_in_queue": position}
