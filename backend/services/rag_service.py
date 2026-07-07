import asyncio
import hashlib
import json
import logging
from typing import List, Dict, Tuple, Optional

import chromadb
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.document import Document
from backend.utils.config import settings
from backend.services.ollama_client import get_ollama_client
from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)


RAG_SYSTEM_PROMPT = """Ты — ИТ-бот поддержки. Отвечай на вопросы сотрудников, используя только предоставленную информацию из базы знаний.
Если информации недостаточно для ответа, скажи об этом и предложи создать заявку в службу поддержки.

Ответ должен быть строго официальным, лаконичным и профессиональным. Не используй эмодзи, сленг или излишнюю вежливость. 
Не выводи процесс рассуждений (Thinking...), не объясняй свои действия — выдавай только готовый ответ."""


class RAGService:
    """RAG (Retrieval-Augmented Generation) service with ChromaDB + Ollama."""

    def __init__(self, ollama=None):
        self._chroma_host = settings.chroma_db_host
        self._chroma_port = settings.chroma_db_port
        self._top_k = settings.rag_top_k
        self._cache_ttl = settings.ai_cache_ttl
        
        self._client = chromadb.HttpClient(host=self._chroma_host, port=self._chroma_port)
        self._collection = self._client.get_or_create_collection("it_knowledge_base")
        
        self._ollama = ollama

    @staticmethod
    async def _embed_batch(ollama_client, texts: List[str], concurrency: int = 5) -> List[List[float]]:
        """Generate embeddings for multiple texts in parallel with rate limiting."""
        sem = asyncio.Semaphore(concurrency)

        async def _emb(text: str):
            async with sem:
                return await ollama_client.embed(text)

        embeddings = await asyncio.gather(*[_emb(t) for t in texts])
        return list(embeddings)

    async def index_document(self, document_id: int, filename: str, chunks: List[str], metadata_extra: Optional[List[Dict]] = None) -> int:
        """Index document chunks into ChromaDB. Returns total chunks stored."""
        ollama = self._ollama or get_ollama_client()

        ids = [f"doc_{document_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = []
        for i in range(len(chunks)):
            meta = {"document_id": document_id, "filename": filename, "chunk_index": i}
            if metadata_extra and i < len(metadata_extra):
                meta.update(metadata_extra[i])
            metadatas.append(meta)
        
        embeddings = await self._embed_batch(ollama, chunks)
        
        self._collection.add(ids=ids, documents=chunks, metadatas=metadatas, embeddings=embeddings)
        logger.info(f"Indexed document {document_id} ({filename}): {len(chunks)} chunks")
        return len(chunks)

    async def query(
        self,
        question: str,
        top_k: Optional[int] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> Tuple[str, List[Dict]]:
        """Query the RAG pipeline. Returns (answer_text, sources_list).

        Args:
            question: User's current question.
            top_k: Number of document chunks to retrieve.
            conversation_history: List of {"question": ..., "answer": ...} pairs for multi-turn context.
        """
        k = top_k or self._top_k
        
        cache_key = f"rag:{hashlib.md5(question.lower().encode()).hexdigest()}"
        _r = await get_redis()
        cached = await _r.get(cache_key)
        if cached:
            data = json.loads(cached)
            return data["answer"], data["sources"]
        
        ollama = self._ollama or get_ollama_client()
        question_embedding = await ollama.embed(question)
        
        results = self._collection.query(
            query_embeddings=[question_embedding],
            n_results=k,
            include=["documents", "metadatas"],
        )
        
        if not results["documents"] or not results["documents"][0]:
            return "Извините, я не нашёл информации по этому вопросу в базе знаний. Пожалуйста, создайте заявку в ИТ-поддержку.", []
        
        context_chunks = results["documents"][0]
        sources = []
        for i, meta in enumerate(results["metadatas"][0]):
            sources.append({
                "filename": meta.get("filename", ""),
                "chunk_index": meta.get("chunk_index", 0),
            })
        
        context_text = "\n\n---\n\n".join(context_chunks)

        # Build conversation messages with history
        messages: List[dict] = [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
        ]

        # Add conversation history (last exchanges) — AI remembers context
        if conversation_history:
            for turn in conversation_history[-5:]:  # last 5 Q&A pairs
                messages.append({"role": "user", "content": turn["question"]})
                messages.append({"role": "assistant", "content": turn["answer"]})

        # Current question with context
        messages.append(
            {
                "role": "user",
                "content": f"Контекст из базы знаний:\n\n{context_text}\n\nВопрос: {question}",
            },
        )
        
        answer = await ollama.chat(messages=messages)

        # Only cache single-turn responses — multi-turn depends on conversation context
        if not conversation_history:
            cache_data = json.dumps({"answer": answer, "sources": sources})
            _r = await get_redis()
            await _r.set(cache_key, cache_data, ex=self._cache_ttl)

        return answer, sources

    async def search_templates(self, query: str, limit: int = 5) -> List[Dict]:
        """Search active templates by filename and description.

        Two modes:
          - Download intent: "скачать" + any other word → matches templates where that word
            appears in filename or description (ignoring "скачать" itself).
          - Regular search: any word from query OR-matched against filename/description.
        Returns list of dicts with id, filename, description, file_type.
        """
        from sqlalchemy import func as sa_func

        terms = [t.strip() for t in query.split() if len(t.strip()) > 1]
        if not terms:
            return []

        conditions = [Document.is_template == True, Document.is_active == True]
        download_intent = "скачать" in {t.lower() for t in terms}

        if download_intent:
            other_terms = [t for t in terms if t.lower() not in ("скачать", "шаблон")]
            effective_terms = other_terms if other_terms else terms
        else:
            effective_terms = terms

        like_conditions = []
        for term in effective_terms:
            like_conditions.append(
                or_(
                    Document.filename.ilike(f"%{term}%"),
                    sa_func.coalesce(Document.description, "").ilike(f"%{term}%"),
                )
            )

        if like_conditions:
            stmt = (
                select(Document)
                .where(*conditions, or_(*like_conditions))
                .order_by(Document.id.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(Document)
                .where(*conditions)
                .order_by(Document.id.desc())
                .limit(limit)
            )

        async with async_session_factory() as session:
            result = await session.execute(stmt)
            templates = result.scalars().all()

        return [
            {
                "id": t.id,
                "filename": t.filename,
                "description": t.description,
                "file_type": t.file_type,
            }
            for t in templates
        ]

    async def delete_document(self, document_id: int):
        """Delete all vectors for a document from ChromaDB."""
        where_clause = {"document_id": document_id}
        self._collection.delete(where=where_clause)
        logger.info(f"Deleted document {document_id} from vector store")


_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Get or create the RAG service instance."""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
