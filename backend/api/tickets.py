"""Ticket management API — local ticket storage."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, status, UploadFile, Query
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models.ticket import Ticket, TicketReply
from backend.utils.rate_limiter import admin_rate_limit_dependency
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


def _check_internal_api_key(x_internal_token: Optional[str] = Header(None)) -> None:
    """Verify the X-Internal-Token header matches configured API key."""
    from backend.utils.config import settings

    if not settings.internal_api_key:
        return
    if x_internal_token != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


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


@router.post("/create", response_model=CreateTicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    req: CreateTicketRequest,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Create a local ticket record. Called before Bitrix24 deal creation."""
    from backend.api.bot_internal import normalize_phone

    ticket = Ticket(
        full_name=req.full_name,
        phone=normalize_phone(req.phone),
        department=req.department,
        category=req.category,
        description=req.description,
        photo_urls=req.photo_urls or [],
        status="new",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)

    return CreateTicketResponse(ticket_id=ticket.id, status="new")


class TicketListResponse(BaseModel):
    tickets: list[dict]


@router.get("/list", response_model=TicketListResponse)
async def list_tickets(
    phone: str = "",
    limit: int = 5,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List tickets by phone number (most recent first)."""
    from backend.api.bot_internal import normalize_phone

    normalized = normalize_phone(phone) if phone else ""

    stmt = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit)
    if normalized:
        stmt = stmt.where(Ticket.phone == normalized)

    result = await db.execute(stmt)
    tickets = result.scalars().all()

    return TicketListResponse(
        tickets=[
            {
                "id": t.id,
                "category": t.category,
                "status": t.status,
                "description": (t.description or "")[:200],
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tickets
        ]
    )


class LinkDealRequest(BaseModel):
    ticket_id: int
    bitrix_deal_id: int


@router.post("/link-deal")
async def link_deal(
    req: LinkDealRequest,
    _internal: None = Depends(_check_internal_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Link a local ticket to its Bitrix24 deal ID after creation."""
    result = await db.execute(select(Ticket).where(Ticket.id == req.ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {req.ticket_id} not found",
        )

    ticket.bitrix_deal_id = req.bitrix_deal_id
    await db.commit()
    return {"ticket_id": req.ticket_id, "bitrix_deal_id": req.bitrix_deal_id}


# --- Admin endpoints (JWT protected) ---

from backend.utils.auth_deps import require_admin, require_admin_void as _require_admin

VALID_STATUSES = {"new", "in_progress", "done", "resolved", "closed", "pending_sync"}


class AdminTicketDetail(BaseModel):
    id: int
    full_name: str
    phone: str
    department: str
    category: str
    description: Optional[str] = ""
    photo_urls: list = []
    bitrix_deal_id: Optional[int] = None
    status: str
    closed_by_user: bool = False
    created_at: Optional[str] = None


@router.get("/admin/list", response_model=list[AdminTicketDetail])
async def admin_list_tickets(
    status_filter: Optional[str] = Query(None),
    search: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """List all tickets for admin panel."""
    stmt = select(Ticket)
    
    if status_filter and status_filter in VALID_STATUSES:
        stmt = stmt.where(Ticket.status == status_filter)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (Ticket.full_name.ilike(like)) |
            (Ticket.phone.ilike(like)) |
            (Ticket.category.ilike(like))
        )

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar()

    stmt = stmt.order_by(Ticket.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    return [AdminTicketDetail(
        id=t.id,
        full_name=t.full_name,
        phone=t.phone,
        department=t.department or "",
        category=t.category,
        description=t.description,
        photo_urls=t.photo_urls or [],
        bitrix_deal_id=t.bitrix_deal_id,
        status=t.status,
        closed_by_user=t.closed_by_user or False,
        created_at=t.created_at.isoformat() if t.created_at else None,
    ) for t in tickets]


class UpdateStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)


@router.post("/admin/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: int,
    req: UpdateStatusRequest,
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Update ticket status. Valid values: new, in_progress, done, closed."""
    if req.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{req.status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found",
        )

    old_status = ticket.status
    ticket.status = req.status
    await db.commit()

    return {"ticket_id": ticket_id, "old_status": old_status, "new_status": req.status}


class AnalyticsResponse(BaseModel):
    total: int
    by_department: dict[str, int]
    by_status: dict[str, int]
    by_month: list[dict]
    tickets: list[AdminTicketDetail]
    tickets_total: int


def _build_date_range(period_type: str, period_value: str, year: int, from_date: str = "", to_date: str = ""):
    """Return (start_datetime, end_datetime) based on period selection."""
    import datetime as dt

    def parse_date(s):
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    if period_type == "custom" and from_date and to_date:
        s = parse_date(from_date) or dt.datetime(year=2020, month=1, day=1)
        e = parse_date(to_date) or dt.datetime(year=2099, month=12, day=31, hour=23, minute=59, second=59)
        return s, e

    if period_type == "all":
        return dt.datetime(2020, 1, 1), dt.datetime(2099, 12, 31, 23, 59, 59)

    if period_type == "year":
        return dt.datetime(year, 1, 1), dt.datetime(year, 12, 31, 23, 59, 59)

    if period_type == "quarter":
        q = int(period_value) if period_value else 1
        month_start = (q - 1) * 3 + 1
        month_end = q * 3
        return dt.datetime(year, month_start, 1), dt.datetime(year, month_end, 31, 23, 59, 59)

    if period_type == "half_year":
        h = int(period_value) if period_value else 1
        if h == 1:
            return dt.datetime(year, 1, 1), dt.datetime(year, 6, 30, 23, 59, 59)
        return dt.datetime(year, 7, 1), dt.datetime(year, 12, 31, 23, 59, 59)

    if period_type == "month":
        m = int(period_value) if period_value else 1
        import calendar
        last_day = calendar.monthrange(year, m)[1]
        return dt.datetime(year, m, 1), dt.datetime(year, m, last_day, 23, 59, 59)

    now = dt.datetime.now()
    y = now.year
    return dt.datetime(y, 1, 1), dt.datetime(y, 12, 31, 23, 59, 59)


class AdminStatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]


@router.get("/admin/stats", response_model=AdminStatsResponse)
async def admin_ticket_stats(
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Get ticket count statistics for dashboard."""
    total_result = await db.execute(select(func.count(Ticket.id)))
    total = total_result.scalar()

    status_result = await db.execute(
        select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    return AdminStatsResponse(total=total, by_status=by_status)


@router.get("/admin/analytics", response_model=AnalyticsResponse)
async def admin_ticket_analytics(
    period_type: str = Query("year"),
    period_value: str = Query(""),
    year: int = Query(0),
    from_date: str = Query(""),
    to_date: str = Query(""),
    department_filter: str = Query(""),
    status_filter: str = Query(""),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Get ticket analytics aggregates + paginated tickets list."""
    import datetime as dt

    try:
        if year == 0:
            year = dt.datetime.now().year

        start_dt, end_dt = _build_date_range(period_type, period_value, year, from_date, to_date)

        base_where = (Ticket.created_at >= start_dt) & (Ticket.created_at <= end_dt)
        if department_filter:
            base_where = base_where & Ticket.department.ilike(f"%{department_filter}%")
        if status_filter and status_filter in VALID_STATUSES:
            base_where = base_where & (Ticket.status == status_filter)

        # Total count
        total_result = await db.execute(select(func.count(Ticket.id)).where(base_where))
        total = total_result.scalar() or 0

        # By department
        dept_result = await db.execute(
            select(Ticket.department, func.count(Ticket.id).label("cnt"))
            .where(base_where)
            .group_by(Ticket.department)
            .order_by(func.count(Ticket.id).desc())
        )
        by_department = {(row[0] or "Не указан"): row[1] for row in dept_result.all()}

        # By status
        status_agg_result = await db.execute(
            select(Ticket.status, func.count(Ticket.id).label("cnt"))
            .where(base_where)
            .group_by(Ticket.status)
        )
        by_status = {(row[0] or "unknown"): row[1] for row in status_agg_result.all()}

        # By month (YYYY-MM format) — subquery to avoid psycopg3 strict_names issue
        _subq = select(
            func.date_trunc("month", Ticket.created_at).label("m"),
            Ticket.id,
        ).where(base_where).subquery()

        month_result = await db.execute(
            select(_subq.c.m, func.count(_subq.c.id).label("cnt"))
            .group_by(_subq.c.m)
            .order_by(_subq.c.m)
        )
        by_month = []
        for row in month_result.all():
            m_str = row[0].strftime("%Y-%m") if hasattr(row[0], "strftime") else str(row[0])[:7]
            by_month.append({"month": m_str, "count": row[1]})

        # Paginated tickets list
        sort_col_map = {
            "created_at": Ticket.created_at,
            "full_name": Ticket.full_name,
            "department": Ticket.department,
            "category": Ticket.category,
            "status": Ticket.status,
        }
        sort_col = sort_col_map.get(sort_by, Ticket.created_at)
        order_clause = sort_col.desc() if sort_order == "desc" else sort_col.asc()

        tickets_stmt = (
            select(Ticket).where(base_where).order_by(order_clause).offset(offset).limit(limit)
        )
        tickets_result = await db.execute(tickets_stmt)
        tickets = tickets_result.scalars().all()

        # Tickets total — already computed above as `total`
        tickets_total = total

        return AnalyticsResponse(
            total=total,
            by_department=by_department,
            by_status=by_status,
            by_month=by_month,
            tickets=[AdminTicketDetail(
                id=t.id,
                full_name=t.full_name,
                phone=t.phone,
                department=t.department or "",
                category=t.category,
                description=t.description,
                photo_urls=t.photo_urls or [],
                bitrix_deal_id=t.bitrix_deal_id,
                status=t.status,
                closed_by_user=bool(t.closed_by_user),
                created_at=t.created_at.isoformat() if t.created_at else None,
            ) for t in tickets],
            tickets_total=tickets_total,
        )
    except Exception as e:
        logger.exception("Analytics endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка обработки аналитики. Попробуйте позже.",
        ) from e


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)


@router.delete("/admin/delete")
async def admin_bulk_delete_tickets(
    req: BulkDeleteRequest,
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Hard delete tickets from the database."""
    deleted = 0
    not_found = []
    for tid in req.ids:
        result = await db.execute(select(Ticket).where(Ticket.id == tid))
        ticket = result.scalar_one_or_none()
        if ticket:
            # Удалить связанные ответы (foreign key constraint)
            await db.execute(delete(TicketReply).where(TicketReply.ticket_id == tid))
            await db.delete(ticket)
            deleted += 1
        else:
            not_found.append(tid)

    await db.commit()

    return {
        "deleted": deleted,
        "not_found": not_found,
        "message": f"Удалено заявок: {deleted}" + (f", не найдено: {len(not_found)}" if not_found else ""),
    }


@router.get("/admin/{ticket_id}", response_model=AdminTicketDetail)
async def admin_get_ticket(
    ticket_id: int,
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Get single ticket details."""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found",
        )

    return AdminTicketDetail(
        id=ticket.id,
        full_name=ticket.full_name,
        phone=ticket.phone,
        department=ticket.department or "",
        category=ticket.category,
        description=ticket.description,
        photo_urls=ticket.photo_urls or [],
        bitrix_deal_id=ticket.bitrix_deal_id,
        status=ticket.status,
        closed_by_user=ticket.closed_by_user or False,
        created_at=ticket.created_at.isoformat() if ticket.created_at else None,
    )


# --- Reply endpoints ---

@router.post("/admin/{ticket_id}/reply")
async def admin_reply_to_ticket(
    ticket_id: int,
    text: str = Form(..., min_length=1, max_length=4096),
    files: list[UploadFile] = File(default=[]),
    _admin: str = Depends(require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Admin replies to ticket with optional file attachments. Saves reply to DB and sends notification via bot."""
    from backend.utils.config import settings

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} not found",
        )

    # Auto-set status to in_progress
    if ticket.status != "in_progress":
        ticket.status = "in_progress"

    # Collect file names and read bytes for pass-through to bot
    file_names: list[str] = []
    file_data: list[tuple[bytes, str]] = []
    MAX_FILES = 5
    MAX_FILE_SIZE = 10 * 1024 * 1024

    for f in files[:MAX_FILES]:
        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File {f.filename} exceeds 10MB limit",
            )
        file_names.append(f.filename or "unknown")
        file_data.append((content, f.filename or "unknown"))

    # Save reply to DB (filenames only, not stored on disk)
    reply = TicketReply(
        ticket_id=ticket_id,
        admin_name=_admin,
        reply_text=text,
        file_names=file_names if file_names else None,
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)

    # Send notification via bot (fire-and-forget, don't block on failure)
    sent_ok = False
    try:
        from backend.models.allowed_user import AllowedUser
        from backend.utils.phone_utils import normalize_phone

        normalized = normalize_phone(ticket.phone)
        user_id_lookup = await db.execute(
            select(AllowedUser.max_user_id).where(
                (AllowedUser.phone == normalized) & (AllowedUser.max_user_id.isnot(None))
            )
        )
        row = user_id_lookup.one_or_none()
        if not row or not row[0]:
            logger.warning(f"No max_user_id for ticket #{ticket_id}, phone={normalized}")
        else:
            import httpx

            message_text = (
                f'🔔 Ответил: "{_admin}" на заявку #{ticket_id}\n\n'
                f"{text}"
            )
            if file_names:
                message_text += f"\n\n📎 Вложения: {', '.join(file_names)}"
            message_text += "\n\n📢 УВЕДОМЛЕНИЕ: Пожалуйста, не отвечайте на сообщение, оно не будет доставлено."

            async with httpx.AsyncClient(timeout=30) as client:
                form = {"user_id": str(row[0]), "text": message_text}
                resp = await client.post(
                    f"{settings.bot_webhook_url}/bot/send-message",
                    data=form,
                    files=[
                        ("files", (fname, buf))
                        for buf, fname in file_data
                    ],
                    headers={"X-Internal-Token": settings.internal_api_key or ""},
                )
                if resp.status_code == 200:
                    sent_ok = True
    except Exception:
        logger.exception(f"Failed to send reply notification for ticket #{ticket_id}")

    return {"reply_id": reply.id, "ticket_id": ticket_id, "sent_to_max": sent_ok}


@router.get("/admin/{ticket_id}/replies")
async def admin_get_replies(
    ticket_id: int,
    _admin: None = Depends(_require_admin),
    _rl: None = Depends(admin_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Get reply history for a ticket."""
    result = await db.execute(
        select(TicketReply)
        .where(TicketReply.ticket_id == ticket_id)
        .order_by(TicketReply.created_at.asc())
    )
    replies = result.scalars().all()

    return [r.to_dict() for r in replies]



