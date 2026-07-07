import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.rag_query import RAGQuery
from backend.services.rag_service import get_rag_service
from backend.utils.config import settings
from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


def _check_internal_api_key(x_internal_token: Optional[str] = Header(None)) -> None:
    """Verify the X-Internal-Token header matches configured API key."""
    if not settings.internal_api_key:
        return  # Disabled when no key is set (development mode)
    if x_internal_token != settings.internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal token")


async def _check_rate_limit(request: Request):
    """Rate limit RAG query requests by client IP."""
    from backend.utils.rate_limiter import check_rate_limit

    ip = request.client.host if request.client else "unknown"
    await check_rate_limit(request, f"rag_api:{ip}")


class RagQueryRequest(BaseModel):
    query_text: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[int] = None
    conversation_history: Optional[List[Dict]] = Field(default=None, description="List of {question, answer} pairs for multi-turn context")


class SourceItem(BaseModel):
    filename: str
    chunk_index: int


class TemplateItem(BaseModel):
    id: int
    filename: str
    description: Optional[str] = None
    file_type: str


class RagQueryResponse(BaseModel):
    answer: str
    sources: List[SourceItem]
    templates: List[TemplateItem] = []
    cached: bool


async def _log_rag_query(
    user_id: Optional[int],
    query_text: str,
    response_text: Optional[str],
    sources: List[Dict],
    cached: bool,
):
    """Write RAGQuery record to DB in background."""
    async with async_session_factory() as session:
        query_record = RAGQuery(
            user_id=user_id,
            query_text=query_text[:2000],
            response_text=response_text[:5000] if response_text else None,
            sources_used=[s for s in sources[:10]],
            cached=cached,
        )
        session.add(query_record)
        await session.commit()


@router.post("/query", response_model=RagQueryResponse)
async def rag_query(
    req: RagQueryRequest,
    _internal: None = Depends(_check_internal_api_key),
    _rate_limit: None = Depends(_check_rate_limit),
):
    """RAG knowledge base search endpoint.

    Accepts a natural language question, retrieves relevant document chunks from ChromaDB,
    and generates an answer via Ollama LLM. Responses are cached in Redis for single-turn queries.

    Supports multi-turn conversation via `conversation_history` — previous Q&A pairs are included
    in the AI prompt so follow-up questions ("расскажи подробнее") have context.

    Public bot-facing endpoint — protected by X-Internal-Token header.
    Rate limited to 60 requests per minute per IP.
    """
    rag = get_rag_service()
    query_lower = req.query_text.lower()
    download_intent = "скачать" in query_lower or "download" in query_lower

    answer: str = ""
    sources: List[Dict] = []
    templates_found: List[Dict] = []
    cached_exists = False

    try:
        if download_intent:
            templates_found = await rag.search_templates(req.query_text)

            if templates_found:
                template_names = "\n".join([f"• {t['filename']}" for t in templates_found[:5]])
                answer = f"📎 Вот найденные шаблоны для скачивания:\n\n{template_names}"
                sources = []
                cached_exists = False
            else:
                templates_found = []
                answer, sources = await rag.query(
                    req.query_text,
                    conversation_history=req.conversation_history,
                )
                cache_key = f"rag:{hashlib.md5(query_lower.encode()).hexdigest()}"
                _r = await get_redis()
                cached_exists = bool(await _r.get(cache_key))
        else:
            answer, sources = await rag.query(
                req.query_text,
                conversation_history=req.conversation_history,
            )
            templates_found = []  # <-- не искать шаблоны без триггера "скачать"

            cache_key = f"rag:{hashlib.md5(query_lower.encode()).hexdigest()}"
    except Exception:
        logger.exception("RAG query failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG service unavailable (Ollama/ChromaDB error)",
        )

    asyncio.create_task(
        _log_rag_query(
            user_id=req.user_id,
            query_text=req.query_text,
            response_text=answer,
            sources=sources,
            cached=cached_exists,
        )
    )

    return RagQueryResponse(
        answer=answer,
        sources=[SourceItem(filename=s["filename"], chunk_index=s["chunk_index"]) for s in sources],
        templates=[TemplateItem(**t) for t in templates_found],
        cached=cached_exists,
    )


@router.get("/health")
async def rag_health():
    """Health check for RAG dependencies: ChromaDB, Ollama, Redis."""
    from backend.services.ollama_client import get_ollama_client

    result = {"chromadb": False, "ollama": False, "redis": False}

    try:
        rag = get_rag_service()
        rag._collection.count()
        result["chromadb"] = True
    except Exception as e:
        result["chromadb_error"] = str(e)

    try:
        ollama = get_ollama_client()
        await ollama.embed("test")
        result["ollama"] = True
    except Exception as e:
        result["ollama_error"] = str(e)

    try:
        _r = await get_redis()
        await _r.ping()
        result["redis"] = True
    except Exception as e:
        result["redis_error"] = str(e)

    return result
