from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.admin import Admin
from backend.utils.auth_jwt import hash_password
from backend.utils.auth_deps import require_admin


router = APIRouter(prefix="/api/v1/admins", tags=["admin management"])


class AdminListResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    per_page: int


class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = Field(None, max_length=500)


class AdminUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None


@router.get("/list", response_model=AdminListResponse)
async def list_admins(
    page: int = 1,
    per_page: int = 50,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    count_result = await db.execute(select(func.count(Admin.id)).where(Admin.is_active == True))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(
        select(Admin).where(Admin.is_active == True).order_by(Admin.id).offset(offset).limit(per_page)
    )
    admins = result.scalars().all()

    return AdminListResponse(
        items=[a.to_dict() for a in admins],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/add", status_code=status.HTTP_201_CREATED)
async def add_admin(
    req: AdminCreateRequest,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    existing = await db.execute(select(Admin).where(Admin.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    admin = Admin(
        username=req.username,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    return {"message": "Admin created", "admin": admin.to_dict()}


@router.put("/{admin_id}")
async def update_admin(
    admin_id: int,
    req: AdminUpdateRequest,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin not found")

    if req.full_name is not None:
        admin.full_name = req.full_name
    if req.is_active is not None:
        admin.is_active = req.is_active

    await db.commit()
    await db.refresh(admin)

    return {"message": "Admin updated", "admin": admin.to_dict()}


@router.delete("/{admin_id}")
async def delete_admin(
    admin_id: int,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    if admin_id == 2:
        raise HTTPException(status_code=403, detail="Initial administrator cannot be deleted")

    result = await db.execute(select(Admin).where(Admin.id == admin_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin not found")

    admin.is_active = False
    await db.commit()

    return {"message": "Admin deactivated", "admin": admin.to_dict()}
