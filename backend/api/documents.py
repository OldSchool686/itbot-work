import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, status, UploadFile, File, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.department import Department
from backend.models.document import Document
from backend.services.document_parser import DocumentParser
from backend.services.rag_service import get_rag_service
from backend.utils.auth_deps import require_admin as get_current_admin
from backend.utils.config import settings

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


SUPPORTED_TYPES = {"pdf", "docx", "xlsx", "txt", "md", "odt", "xls", "doc"}

DOCUMENTS_BASE = "/app/documents"
UPLOAD_DIR = DOCUMENTS_BASE
TEMPLATE_DIR = os.path.join(DOCUMENTS_BASE, "templates")
DOCUMENT_MAX_SIZE = 50 * 1024 * 1024
TEMPLATE_MAX_SIZE = 10 * 1024 * 1024


class DocumentListItem(BaseModel):
    id: int
    filename: str
    file_type: str
    size_bytes: Optional[int] = None
    chunks_count: int
    uploaded_by: Optional[str] = None
    is_active: bool = False
    created_at: Optional[str] = None
    is_template: bool = False
    description: Optional[str] = None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        if isinstance(obj, dict):
            for f in ("is_active", "is_template"):
                if obj.get(f) is None:
                    obj[f] = False
        return super().model_validate(obj, **kwargs)


class DocumentListResponse(BaseModel):
    items: List[DocumentListItem]
    total: int
    page: int
    per_page: int


class DocumentUploadResponse(BaseModel):
    document_id: int
    filename: str
    chunks_count: int
    status: str


async def _index_document_background(document_id: int, filename: str, chunks: List[str]):
    """Index document chunks into ChromaDB in background."""
    try:
        rag = get_rag_service()
        await rag.index_document(document_id=document_id, filename=filename, chunks=chunks)
        logger.info(f"Background indexing completed for document {document_id}")
    except Exception:
        logger.exception(f"Failed to index document {document_id} in background")


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    is_template: bool = Form(False),
    description: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document for RAG indexing or a template for download.

    Flow for documents: save file → parse → create DB record → return immediately → index in background.
    Flow for templates: save file to templates dir → create DB record → skip parsing/indexing.
    Requires valid JWT Bearer token.
    """
    username = current_admin

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File name is required")

    file_ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if file_ext not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{file_ext}'. Supported: {', '.join(sorted(SUPPORTED_TYPES))}",
        )

    content = await file.read()

    if is_template:
        if len(content) > TEMPLATE_MAX_SIZE:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Template too large (max 10MB)")
        store_dir = TEMPLATE_DIR
    else:
        if len(content) > DOCUMENT_MAX_SIZE:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large (max 50MB)")
        store_dir = UPLOAD_DIR

    os.makedirs(store_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    file_hash = hashlib.md5(f"{file.filename}{timestamp}".encode()).hexdigest()[:12]
    safe_filename = f"{file_hash}.{file_ext}"
    file_path = os.path.realpath(os.path.join(store_dir, safe_filename))
    if not file_path.startswith(os.path.realpath(store_dir)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    chunks: List[str] = []

    if not is_template:
        parser = DocumentParser(chunk_size=settings.rag_chunk_size, overlap=settings.rag_chunk_overlap)
        chunks = await parser.parse(file_path, file_ext)

    doc_record = Document(
        filename=file.filename,
        original_path=safe_filename,
        file_type=file_ext,
        size_bytes=len(content),
        chunks_count=len(chunks),
        uploaded_by=username,
        is_active=True,
        is_template=is_template,
        description=description,
    )
    db.add(doc_record)
    await db.commit()
    await db.refresh(doc_record)

    if not is_template:
        background_tasks.add_task(
            _index_document_background,
            document_id=doc_record.id,
            filename=file.filename,
            chunks=chunks,
        )

    return DocumentUploadResponse(
        document_id=doc_record.id,
        filename=file.filename,
        chunks_count=len(chunks),
        status="indexed" if not is_template else "template",
    )


@router.get("/list", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    status_filter: str = Query("all"),  # "all", "active", "inactive"
    is_template: Optional[str] = Query(None),  # "true", "false", or None for all
    current_admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List documents (paginated). Requires valid JWT Bearer token.

    status_filter: 'all' (default) — все, 'active' — только активные, 'inactive' — только отключённые.
    is_template: 'true' — only templates, 'false' — only non-template docs, None or omit for all.
    """

    where_clauses = []

    if status_filter == "active":
        where_clauses.append(Document.is_active == True)
    elif status_filter == "inactive":
        where_clauses.append(Document.is_active == False)

    if is_template == "true":
        where_clauses.append(Document.is_template == True)
    elif is_template == "false":
        where_clauses.append(Document.is_template == False)

    base_where = None
    if where_clauses:
        base_where = where_clauses[0]
        for clause in where_clauses[1:]:
            base_where = base_where & clause

    count_query = select(func.count(Document.id))
    if base_where is not None:
        count_query = count_query.where(base_where)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    stmt = select(Document).order_by(Document.id.desc()).offset(offset).limit(per_page)
    if base_where is not None:
        stmt = stmt.where(base_where)
    result = await db.execute(stmt)
    documents = result.scalars().all()

    return DocumentListResponse(
        items=[DocumentListItem(**d.to_dict()) for d in documents],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.patch("/{document_id}")
async def update_template(
    document_id: int,
    description: Optional[str] = Form(None),
    current_admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update document description."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if description is not None:
        doc.description = description

    await db.commit()
    await db.refresh(doc)

    return {"message": "Template updated", "document": doc.to_dict()}


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    permanent: bool = Query(False),
    current_admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate (soft) or permanently remove a document.

    Without ?permanent=true — soft delete (is_active=False).
    With ?permanent=true — hard delete (row removed, file deleted from disk, ChromaDB cleaned).
    Requires valid JWT Bearer token.
    """
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    rag = get_rag_service()
    await rag.delete_document(document_id=document_id)

    if permanent:
        import aiofiles as _aiofiles
        file_to_remove = None
        if doc.is_template:
            file_to_remove = os.path.realpath(os.path.join(TEMPLATE_DIR, doc.original_path))
        else:
            file_to_remove = os.path.realpath(os.path.join(UPLOAD_DIR, doc.original_path))
        if file_to_remove and os.path.isfile(file_to_remove):
            try:
                os.remove(file_to_remove)
            except OSError as e:
                logger.warning(f"Failed to remove file {file_to_remove}: {e}")
        await db.delete(doc)
        await db.commit()
        return {"message": "Document permanently deleted", "document_id": document_id}
    else:
        doc.is_active = False
        await db.commit()
        return {"message": "Document deactivated", "document": doc.to_dict()}


department_router = APIRouter(prefix="/api/v1", tags=["departments"])


class DepartmentSuggestItem(BaseModel):
    name: str
    type: str


@department_router.get("/department-suggest")
async def department_suggest(
    q: str = Query("", min_length=0, max_length=200),
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Auto-suggest departments by partial name match."""
    from backend.utils.redis_pool import get_redis

    cache_key = f"dept_suggest:{q.lower()}:{limit}"
    _r = await get_redis()
    cached = await _r.get(cache_key)
    if cached:
        return json.loads(cached)

    stmt = select(Department).where(
        Department.is_active == True,
        Department.name.ilike(f"%{q}%"),
    ).order_by(Department.name).limit(limit)

    result = await db.execute(stmt)
    departments = result.scalars().all()

    results = [{"name": d.name, "type": d.type} for d in departments]
    cache_data = json.dumps(results)
    await _r.set(cache_key, cache_data, ex=300)

    return results
