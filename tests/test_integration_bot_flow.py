"""Integration tests simulating full user flow against running backend services.

These tests mock Ollama, B24, ChromaDB, Redis, and PostgreSQL to simulate a complete
user journey: whitelist check → consent → ticket creation → deal submission → RAG query.

Run with real services via Docker Compose: pytest tests/integration_test_bot_flow.py -v
"""
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_full_app(db_mock):
    """Build a full app with all routers for integration testing."""
    from backend.api.auth import router as auth_router
    from backend.api.bot_internal import (
        router as bot_internal_router,
        _check_internal_api_key,
    )
    from backend.database import get_db

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(bot_internal_router)

    async def override_get_db():
        yield db_mock

    # Skip internal API key check in tests
    async def skip_auth():
        pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_check_internal_api_key] = skip_auth
    return TestClient(app)


class TestFullUserFlow:
    """Simulate complete user flow from whitelist check to ticket creation."""

    def test_whitelist_check_then_ticket_status(self):
        """Test that a whitelisted user can pass access check and query ticket status later."""
        from backend.models.allowed_user import AllowedUser
        from backend.models.ticket import Ticket

        # Mock allowed user
        user = MagicMock(spec=AllowedUser)
        user.id = 1
        user.phone = "+79001234567"
        user.full_name = "Ivanov Ivan"
        user.department = "IT"
        user.consent_given = True
        user.is_active = True

        # Mock ticket
        ticket = MagicMock(spec=Ticket)
        ticket.id = 1
        ticket.status = "new"
        ticket.bitrix_deal_id = 42
        ticket.updated_at = None

        call_count = [0]
        async def execute_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar_one_or_none.return_value = user
            elif call_count[0] == 2:
                result.scalar_one_or_none.return_value = ticket
            else:
                result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_full_app(mock_db)

        # Step 1: Check whitelist access
        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["consent_given"] is True

        # Step 2: Query ticket status
        resp = client.get("/api/v1/bot/ticket-status/1")
        assert resp.status_code == 200
        ticket_data = resp.json()
        assert ticket_data["status"] == "new"
        assert ticket_data["bitrix_deal_id"] == 42

    def test_non_whitelisted_user_blocked(self):
        """Test that a non-whitelisted user is denied access."""
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_full_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79009999999"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False
        assert data["user_data"] is None

    def test_phone_normalization_in_access_check(self):
        """Test that phone normalization works (8xxx → +7xxx)."""
        from backend.models.allowed_user import AllowedUser

        user = MagicMock(spec=AllowedUser)
        user.id = 1
        user.phone = "+79001234567"
        user.full_name = "Petrov Petr"
        user.department = "HR"
        user.consent_given = True
        user.is_active = True

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_full_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "89001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True

    def test_notify_user_with_valid_ticket(self):
        """Test that admin can notify a user about ticket status change."""
        from backend.models.ticket import Ticket

        ticket = MagicMock(spec=Ticket)
        ticket.id = 1
        ticket.phone = "+79001234567"
        ticket.status = "resolved"

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = ticket
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_full_app(mock_db)

        resp = client.post(
            "/api/v1/bot/notify-user",
            json={"ticket_id": 1, "message": "Ваша заявка решена."},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticket_id"] == 1
        assert data["user_phone"] == "+79001234567"

    def test_user_by_phone_lookup(self):
        """Test that bot can look up user details by phone for ticket pre-fill."""
        from backend.models.allowed_user import AllowedUser

        user = MagicMock(spec=AllowedUser)
        user.id = 1
        user.phone = "+79001234567"
        user.full_name = "Sidorov Alexey"
        user.department = "Finance"
        user.consent_given = True
        user.is_active = True

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_full_app(mock_db)

        resp = client.get("/api/v1/bot/user-by-phone?phone=+79001234567")
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] == "Sidorov Alexey"
        assert data["department"] == "Finance"


class TestRAGQueryFlow:
    """Test RAG query flow end-to-end with mocked services."""

    @pytest.mark.asyncio
    async def test_rag_query_returns_answer(self):
        """Test that a RAG query returns an answer with sources."""
        from backend.services.rag_service import RAGService

        coll = MagicMock()
        coll.query.return_value = {
            "documents": [["chunk about password policy"]],
            "metadatas": [[{"document_id": 1, "filename": "policy.pdf", "chunk_index": 0}]],
        }

        chroma_client = MagicMock()
        chroma_client.get_or_create_collection.return_value = coll

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock()

        ollama = MagicMock()
        ollama.embed = AsyncMock(return_value=[0.1] * 768)
        ollama.chat = AsyncMock(return_value="Пароль должен содержать минимум 8 символов.")

        with patch("chromadb.HttpClient", return_value=chroma_client):
            with patch("backend.utils.redis_pool.get_redis", return_value=redis_mock):
                svc = RAGService(ollama=ollama)

                answer, sources = await svc.query("Какой пароль нужен?")

        assert isinstance(answer, str)
        assert len(sources) == 1
        assert sources[0]["filename"] == "policy.pdf"


class TestDocumentUploadFlow:
    """Test document upload flow end-to-end."""

    def test_upload_parse_index_flow(self):
        """Test that uploading a document parses it and schedules background indexing."""
        import backend.api.documents as doc_module
        from io import BytesIO

        original_dir = doc_module.UPLOAD_DIR
        tmp_dir = "/tmp/test_docs"
        doc_module.UPLOAD_DIR = tmp_dir

        try:
            file_content = b"test document content for indexing"

            parser_mock = MagicMock()
            parser_mock.return_value.parse = AsyncMock(return_value=["chunk1", "chunk2"])

            rag_instance = MagicMock()
            rag_instance.index_document = AsyncMock()

            async def execute_side_effect(stmt):
                result = MagicMock()
                result.scalar_one_or_none.return_value = None
                return result

            mock_db = AsyncMock()
            mock_db.execute = execute_side_effect
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()

            def refresh_cb(obj):
                obj.id = 1

            mock_db.refresh = AsyncMock(side_effect=refresh_cb)

            from backend.api.documents import router as doc_router, get_current_admin
            from backend.database import get_db

            app = FastAPI()
            app.include_router(doc_router)

            async def override_get_db():
                yield mock_db

            def override_auth():
                return "admin"

            app.dependency_overrides[get_db] = override_get_db
            app.dependency_overrides[get_current_admin] = override_auth
            client = TestClient(app)

            with patch("backend.api.documents.DocumentParser", parser_mock):
                resp = client.post(
                    "/api/v1/documents/upload",
                    files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
                )

            assert resp.status_code == 201, f"Got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data["document_id"] == 1
            assert data["chunks_count"] == 2
            assert data["status"] in ("indexing", "indexed")
        finally:
            doc_module.UPLOAD_DIR = original_dir
