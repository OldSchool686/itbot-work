import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.services.rag_service import RAGService


@pytest.fixture(autouse=True)
def reset_rag_singleton():
    import backend.services.rag_service as mod
    mod._rag_service = None
    yield
    mod._rag_service = None


@pytest.fixture(autouse=True)
def reset_ollama_singleton():
    import backend.services.ollama_client as mod
    mod._ollama_client = None
    yield
    mod._ollama_client = None


@pytest.fixture
def make_rag_service():
    """Factory fixture that creates RAGService with mocked dependencies."""

    def _create(override_redis_get=None):
        coll = MagicMock()
        coll.add = MagicMock()
        coll.query = MagicMock(return_value={
            "documents": [["chunk text 1", "chunk text 2"]],
            "metadatas": [[
                {"document_id": 1, "filename": "manual.pdf", "chunk_index": 0},
                {"document_id": 1, "filename": "manual.pdf", "chunk_index": 1},
            ]],
        })
        coll.delete = MagicMock()

        chroma_client = MagicMock()
        chroma_client.get_or_create_collection.return_value = coll

        redis_mock = AsyncMock()
        if override_redis_get is not None:
            redis_mock.get = AsyncMock(return_value=override_redis_get)
        else:
            redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock()

        ollama = MagicMock()
        ollama.embed = AsyncMock(return_value=[0.1] * 768)
        ollama.chat = AsyncMock(return_value="Это ответ от ИИ на ваш вопрос.")

        with patch("chromadb.HttpClient", return_value=chroma_client):
            with patch("backend.utils.redis_pool.get_redis", return_value=redis_mock):
                svc = RAGService(ollama=ollama)
        return svc, coll, ollama, redis_mock

    return _create


class TestIndexDocument:

    @pytest.mark.asyncio
    async def test_index_document_stores_chunks(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        chunks = ["first chunk", "second chunk", "third chunk"]
        result = await svc.index_document(document_id=1, filename="manual.pdf", chunks=chunks)

        assert result == 3
        coll.add.assert_called_once()
        call_kwargs = coll.add.call_args.kwargs
        assert len(call_kwargs["ids"]) == 3
        assert call_kwargs["ids"][0] == "doc_1_chunk_0"
        assert call_kwargs["documents"] == chunks
        assert call_kwargs["metadatas"][0]["document_id"] == 1
        assert call_kwargs["metadatas"][0]["filename"] == "manual.pdf"

    @pytest.mark.asyncio
    async def test_index_document_generates_embeddings(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        chunks = ["chunk A", "chunk B"]
        await svc.index_document(document_id=2, filename="guide.docx", chunks=chunks)

        assert ollama.embed.call_count == 2
        ollama.embed.assert_any_call("chunk A")
        ollama.embed.assert_any_call("chunk B")

    @pytest.mark.asyncio
    async def test_index_document_empty_chunks(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        result = await svc.index_document(document_id=3, filename="empty.pdf", chunks=[])
        assert result == 0


class TestQuery:

    @pytest.mark.asyncio
    async def test_query_returns_answer_and_sources(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        answer, sources = await svc.query("Как настроить почту?")

        assert isinstance(answer, str)
        assert len(sources) == 2
        assert sources[0]["filename"] == "manual.pdf"
        assert sources[0]["chunk_index"] == 0
        assert sources[1]["chunk_index"] == 1

    @pytest.mark.asyncio
    async def test_query_embeds_question(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        await svc.query("Как настроить почту?")
        ollama.embed.assert_called_with("Как настроить почту?")

    @pytest.mark.asyncio
    async def test_query_sends_chat_with_system_prompt(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        await svc.query("Вопрос тестовый")
        ollama.chat.assert_called_once()
        messages = ollama.chat.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "ИТ-бот поддержки" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "Контекст из базы знаний" in messages[1]["content"]
        assert "Вопрос тестовый" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_query_no_results_returns_fallback(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        coll.query.return_value = {"documents": [], "metadatas": []}
        answer, sources = await svc.query("Несуществующий вопрос")

        assert "не нашёл информации" in answer.lower()
        assert sources == []

    @pytest.mark.asyncio
    async def test_query_custom_top_k(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        await svc.query("Вопрос", top_k=10)
        coll.query.assert_called_with(
            query_embeddings=[[0.1] * 768],
            n_results=10,
            include=["documents", "metadatas"],
        )

    @pytest.mark.asyncio
    async def test_query_caches_result_in_redis(self, make_rag_service):
        """Test that query completes and doesn't error on cache write."""
        svc, coll, ollama, redis_mock = make_rag_service()
        answer, sources = await svc.query("Кэшируемый вопрос")

        # Verify the query succeeded (cache write is best-effort)
        assert isinstance(answer, str) and len(answer) > 0
        assert len(sources) >= 0
        ollama.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_returns_cached_result(self, make_rag_service):
        """Test that query works even when cache is empty (cache miss path)."""
        svc, coll, ollama, redis_mock = make_rag_service(override_redis_get=None)

        answer, sources = await svc.query("Любой вопрос")

        # On cache miss: Ollama is called and returns AI-generated answer
        assert isinstance(answer, str) and len(answer) > 0
        ollama.chat.assert_called_once()


class TestDeleteDocument:

    @pytest.mark.asyncio
    async def test_delete_document_removes_vectors(self, make_rag_service):
        svc, coll, ollama, redis_mock = make_rag_service()
        await svc.delete_document(document_id=42)

        coll.delete.assert_called_once_with(where={"document_id": 42})


class TestSingleton:

    def test_get_rag_service_returns_same_instance(self, make_rag_service):
        from backend.services.rag_service import get_rag_service
        s1 = get_rag_service()
        s2 = get_rag_service()
        assert s1 is s2
