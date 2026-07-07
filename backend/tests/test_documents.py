from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.document import Document
from backend.models.department import Department


def _make_doc_mock(
    id_: int = 1,
    filename: str = "manual.pdf",
    file_type: str = "pdf",
    size_bytes: int = 1024,
    chunks_count: int = 5,
    uploaded_by: str = "admin",
    is_active: bool = True,
):
    doc = MagicMock(spec=Document)
    doc.id = id_
    doc.filename = filename
    doc.original_path = f"20260101_{filename}"
    doc.file_type = file_type
    doc.size_bytes = size_bytes
    doc.chunks_count = chunks_count
    doc.uploaded_by = uploaded_by
    doc.is_active = is_active
    doc.created_at = datetime.now(timezone.utc)

    def to_dict():
        return {
            "id": id_,
            "filename": filename,
            "original_path": f"20260101_{filename}",
            "file_type": file_type,
            "size_bytes": size_bytes,
            "chunks_count": chunks_count,
            "uploaded_by": uploaded_by,
            "is_active": is_active,
            "created_at": doc.created_at.isoformat(),
        }

    doc.to_dict = to_dict
    return doc


def _make_dept_mock(name: str = "IT Department", type_: str = "department"):
    dept = MagicMock(spec=Department)
    dept.id = 1
    dept.name = name
    dept.type = type_
    dept.parent_id = None
    dept.is_active = True
    return dept


def _build_app(db_mock, auth_username="admin"):
    """Build a test app with overridden dependencies."""
    from fastapi import HTTPException, status

    from backend.api.documents import (
        department_router,
        get_current_admin,
        router as doc_router,
    )
    from backend.database import get_db

    app = FastAPI()
    app.include_router(doc_router)
    app.include_router(department_router)

    async def override_get_db():
        yield db_mock

    if auth_username is None:
        def override_auth():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    else:
        def override_auth():
            return auth_username

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_auth
    return TestClient(app)


# --- Upload tests ---

class TestUploadDocument:

    def test_upload_txt_success(self, tmp_path):
        import backend.api.documents as doc_module

        original_dir = doc_module.UPLOAD_DIR
        doc_module.UPLOAD_DIR = str(tmp_path)

        try:
            file_content = b"line 1\n\nline 2\n\nline 3"

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

            with patch("backend.api.documents.DocumentParser", parser_mock):
                with patch("backend.api.documents.get_rag_service", return_value=rag_instance):
                    client = _build_app(mock_db, auth_username="admin")
                    resp = client.post(
                        "/api/v1/documents/upload",
                        files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
                    )

            assert resp.status_code == 201, f"Got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data["document_id"] == 1
            assert data["status"] in ("indexed", "indexing")  # indexing = background task
            assert data["chunks_count"] == 2
        finally:
            doc_module.UPLOAD_DIR = original_dir

    def test_upload_unsupported_type(self):
        mock_db = AsyncMock()
        client = _build_app(mock_db)

        resp = client.post(
            "/api/v1/documents/upload",
            files={"file": ("image.png", BytesIO(b"\x89PNG"), "image/png")},
        )

        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    def test_upload_no_file(self):
        mock_db = AsyncMock()
        client = _build_app(mock_db)

        resp = client.post("/api/v1/documents/upload")
        assert resp.status_code == 422

    def test_upload_unauthorized(self):
        mock_db = AsyncMock()
        client = _build_app(mock_db, auth_username=None)
        file_content = b"some text content here"
        resp = client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )

        assert resp.status_code == 401


# --- List tests ---

class TestListDocuments:

    def test_list_documents_paginated(self):
        docs = [_make_doc_mock(id_=i, filename=f"doc{i}.pdf") for i in range(1, 6)]

        async def execute_side_effect(stmt):
            result = MagicMock()
            if hasattr(stmt, "element"):
                elem_type_str = str(getattr(stmt.element, "type", ""))
                if "INTEGER" in elem_type_str:
                    result.scalar.return_value = len(docs)
                    return result
            result.scalars.return_value.all.return_value = docs
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/documents/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert data["page"] == 1

    def test_list_documents_unauthorized(self):
        mock_db = AsyncMock()
        client = _build_app(mock_db, auth_username=None)

        resp = client.get("/api/v1/documents/list")
        assert resp.status_code == 401


# --- Delete tests ---

class TestDeleteDocument:

    def test_delete_document_success(self):
        doc = _make_doc_mock(id_=5, is_active=True)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = doc
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()

        rag_instance = MagicMock()
        rag_instance.delete_document = AsyncMock()

        client = _build_app(mock_db)

        with patch("backend.api.documents.get_rag_service", return_value=rag_instance):
            resp = client.delete("/api/v1/documents/5")

        assert resp.status_code == 200
        data = resp.json()
        assert "Document deactivated" in data["message"]
        rag_instance.delete_document.assert_called_once_with(document_id=5)

    def test_delete_document_not_found(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.delete("/api/v1/documents/999")
        assert resp.status_code == 404

    def test_delete_document_unauthorized(self):
        mock_db = AsyncMock()
        client = _build_app(mock_db, auth_username=None)

        resp = client.delete("/api/v1/documents/5")
        assert resp.status_code == 401


# --- Department suggest tests ---

class TestDepartmentSuggest:

    def test_suggest_matches(self):
        depts = [
            _make_dept_mock(name="IT Support", type_="department"),
            _make_dept_mock(name="IT Security", type_="service"),
        ]

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = depts
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/department-suggest?q=IT&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] in ("IT Security", "IT Support")

    def test_suggest_empty_query(self):
        depts = [_make_dept_mock(name="Accounting", type_="department")]

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = depts
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/department-suggest?q=&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_suggest_no_matches(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/department-suggest?q=NoSuchDept&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    def test_suggest_respects_limit(self):
        depts = [_make_dept_mock(name=f"Dept {i}") for i in range(10)]

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = depts[:2]
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/department-suggest?q=&limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 2
