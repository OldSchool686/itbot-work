import hashlib
import logging
import os
import secrets
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.document import Document
from backend.utils.rate_limiter import download_rate_limit_dependency
from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

DOCUMENTS_BASE = "/app/documents"
TEMPLATE_DIR = os.path.join(DOCUMENTS_BASE, "templates")
TOKEN_TTL = 3600


@router.post("/{template_id}/generate-link")
async def generate_download_link(
    template_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate a temporary download link for a template.

    Returns a URL with single-use token valid for 1 hour.
    No auth required — callable from bot context.
    """
    result = await db.execute(
        select(Document).where(
            Document.id == template_id,
            Document.is_template == True,
            Document.is_active == True,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.set(f"template_link:{token}", str(doc.id), ex=TOKEN_TTL)

    base_url = os.environ.get("APP_BASE_URL", "http://localhost:8000")
    download_url = f"{base_url}/api/v1/templates/download?token={token}"

    return {"download_url": download_url, "expires_in": TOKEN_TTL}


@router.get("/download")
async def download_template(
    token: str = Query(...),
    _rl: None = Depends(download_rate_limit_dependency),
    db: AsyncSession = Depends(get_db),
):
    """Download a template file using a single-use token.

    Public endpoint — no auth required. Token is consumed after use.
    """
    redis = await get_redis()
    doc_id_str = await redis.get(f"template_link:{token}")
    if not doc_id_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired download link")

    try:
        doc_id = int(doc_id_str)
    except ValueError:
        await redis.delete(f"template_link:{token}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.is_template == True,
            Document.is_active == True,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        await redis.delete(f"template_link:{token}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    await redis.delete(f"template_link:{token}")

    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    file_path = os.path.realpath(os.path.join(TEMPLATE_DIR, doc.original_path))
    if not file_path.startswith(os.path.realpath(TEMPLATE_DIR)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on disk")

    encoded_filename = urllib.parse.quote(doc.filename)
    return FileResponse(
        path=file_path,
        filename=doc.filename,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{encoded_filename}"
            ),
        },
    )

