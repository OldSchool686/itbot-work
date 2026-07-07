import csv
import io
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Header, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.allowed_user import AllowedUser
from backend.utils.auth_deps import require_admin
from backend.utils.phone_utils import normalize_phone


router = APIRouter(prefix="/api/v1/users", tags=["user whitelist"])


def validate_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    if not re.match(r"^\+7\d{10}$", normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid phone format. Expected +7XXXXXXXXXX or 8XXXXXXXXXX",
        )
    return normalized


class UserListResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    per_page: int


class UserCreateRequest(BaseModel):
    phone: str = Field(..., min_length=1)
    full_name: str = Field(..., min_length=1, max_length=500)
    department: Optional[str] = Field(None, max_length=500)
    consent_given: bool = False


class UserUpdateRequest(BaseModel):
    phone: Optional[str] = None
    full_name: Optional[str] = Field(None, max_length=500)
    department: Optional[str] = Field(None, max_length=500)
    consent_given: Optional[bool] = None
    is_active: Optional[bool] = None


@router.get("/list", response_model=UserListResponse)
async def list_users(
    page: int = 1,
    per_page: int = 50,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    department: Optional[str] = None,
    is_active: Optional[bool] = None,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    where_clauses = []
    if name:
        where_clauses.append(AllowedUser.full_name.ilike(f"%{name}%"))
    if phone:
        normalized_phone = normalize_phone(phone)
        where_clauses.append(AllowedUser.phone == normalized_phone)
    if department:
        where_clauses.append(AllowedUser.department.ilike(f"%{department}%"))
    if is_active is not None:
        where_clauses.append(AllowedUser.is_active == is_active)

    query = select(func.count(AllowedUser.id))
    count_query = select(func.count(AllowedUser.id))
    if where_clauses:
        count_query = count_query.where(*where_clauses)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    select_query = select(AllowedUser)
    if where_clauses:
        select_query = select_query.where(*where_clauses)
    select_query = select_query.order_by(AllowedUser.full_name).offset(offset).limit(per_page)

    result = await db.execute(select_query)
    users = result.scalars().all()

    return UserListResponse(
        items=[u.to_dict() for u in users],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("/add", status_code=status.HTTP_201_CREATED)
async def add_user(
    req: UserCreateRequest,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    admin_username = require_admin(authorization, request)
    phone = validate_phone(req.phone)

    existing = await db.execute(select(AllowedUser).where(AllowedUser.phone == phone))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already exists")

    user = AllowedUser(
        phone=phone,
        full_name=req.full_name.strip(),
        department=req.department.strip() if req.department else None,
        consent_given=req.consent_given,
        consent_timestamp=datetime.now(timezone.utc) if req.consent_given else None,
        is_active=True,
        added_by=admin_username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {"message": "User created", "user": user.to_dict()}


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    req: UserUpdateRequest,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    result = await db.execute(select(AllowedUser).where(AllowedUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if req.phone is not None:
        new_phone = validate_phone(req.phone)
        existing_result = await db.execute(select(AllowedUser).where(AllowedUser.phone == new_phone))
        existing_user = existing_result.scalar_one_or_none()
        if existing_user and existing_user.id != user_id:
             raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone already exists")
        user.phone = new_phone

    if req.full_name is not None:
        user.full_name = req.full_name.strip()
    if req.department is not None:
        user.department = req.department.strip() if req.department else None
    if req.consent_given is not None:
        user.consent_given = req.consent_given
        if req.consent_given and not user.consent_timestamp:
            user.consent_timestamp = datetime.now(timezone.utc)
    if req.is_active is not None:
        user.is_active = req.is_active

    await db.commit()
    await db.refresh(user)

    return {"message": "User updated", "user": user.to_dict()}


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    result = await db.execute(select(AllowedUser).where(AllowedUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await db.delete(user)
    await db.commit()

    return {"message": "User deleted"}


@router.put("/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    result = await db.execute(select(AllowedUser).where(AllowedUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    await db.commit()
    await db.refresh(user)

    return {"message": "User deactivated", "user": user.to_dict()}


@router.post("/import-csv")
async def import_users_csv(
    file: UploadFile = File(...),
    mode: str = Form("upsert"),
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    admin_username = require_admin(authorization, request)

    if mode not in ("replace", "upsert"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Mode must be 'replace' or 'upsert'")

    csv_content = await file.read()

    from backend.services.user_importer import import_users_csv as do_import
    result = await do_import(csv_content=csv_content, mode=mode, admin_username=admin_username, db=db)
    return {"message": "Import complete", **result}


@router.get("/export-csv")
async def export_users_csv(
    authorization: Optional[str] = Header(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    require_admin(authorization, request)

    from backend.services.user_importer import export_users_csv as do_export
    csv_data = await do_export(db=db)

    from fastapi.responses import Response
    return Response(
        content=csv_data,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": 'attachment; filename="allowed_users.csv"'},
    )
