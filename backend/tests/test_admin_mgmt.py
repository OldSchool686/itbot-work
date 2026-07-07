from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from backend.models.admin import Admin
from backend.utils.auth_jwt import create_access_token, hash_password


def _make_admin_mock(id_: int = 1, username: str = "admin1", full_name: str = "Admin One", is_active: bool = True):
    admin = MagicMock(spec=Admin)
    admin.id = id_
    admin.username = username
    admin.password_hash = hash_password("password123")
    admin.full_name = full_name
    admin.is_active = is_active
    admin.last_login_at = None
    admin.created_at = None

    def to_dict():
        return {
            "id": id_,
            "username": username,
            "full_name": full_name,
            "is_active": is_active,
            "last_login_at": None,
            "created_at": None,
        }

    admin.to_dict = to_dict
    return admin


def _mock_db_session(admins: list):
    """Create a mock DB session that simulates queries against the given admins list."""
    session = AsyncMock()

    async def execute_side_effect(stmt):
        result = MagicMock()

        # Detect count query
        is_count = False
        raw_cols = getattr(stmt, "_raw_columns", ()) or ()
        for col in raw_cols:
            if type(col).__name__.lower() == "count":
                is_count = True
                break

        if is_count:
            where_criteria = getattr(stmt, "_where_criteria", (None,))
            active_admins = [a for a in admins if a.is_active]
            result.scalar.return_value = len(active_admins)
        else:
            found = admins[:]
            where_clauses = getattr(stmt, "_where_criteria", ()) or ()

            # Filter by is_active if present
            has_is_active_filter = False
            for wc in where_clauses:
                if hasattr(wc, "right") and hasattr(wc.right, "value"):
                    target_val = wc.right.value
                    if hasattr(wc, "left") and getattr(wc.left, "name", None) == "is_active":
                        found = [a for a in admins if a.is_active == target_val]
                        has_is_active_filter = True
                    elif hasattr(wc, "left"):
                        col_name = getattr(wc.left, "name", None)
                        if col_name == "username" and not has_is_active_filter:
                            found = [a for a in admins if a.username == target_val]
                        elif col_name == "id":
                            found = [a for a in admins if a.id == target_val]

            result.scalar_one_or_none.return_value = found[0] if found else None
            result.scalars.return_value.all.return_value = found

        return result

    session.execute.side_effect = execute_side_effect
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def valid_token():
    return create_access_token("admin1")


@pytest.fixture
def client(valid_token):
    """Create a test client with the actual router and mocked DB dependency."""
    from backend.api.admin_mgmt import router as admin_mgmt_router

    app = FastAPI()
    app.include_router(admin_mgmt_router)

    # Override get_db with mock session
    admins = [_make_admin_mock(1, "admin1", "Admin One"), _make_admin_mock(2, "admin2", "Admin Two")]
    mock_session = _mock_db_session(admins)

    async def override_get_db():
        yield mock_session

    app.dependency_overrides[Depends(lambda: None)] = lambda: mock_session

    # We need to override the actual get_db from backend.database
    import backend.main as main_module
    original_router = admin_mgmt_router
    app.include_router(original_router)

    return TestClient(app), admins, mock_session


class TestAdminList:
    def test_list_admins_success(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        admins = [_make_admin_mock(1), _make_admin_mock(2)]
        mock_session = _mock_db_session(admins)

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get(
            "/api/v1/admins/list?page=1&per_page=50",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_list_admins_unauthorized(self):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield _mock_db_session([])

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get("/api/v1/admins/list")
        assert resp.status_code == 401

    def test_list_admins_invalid_token(self):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield _mock_db_session([])

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get(
            "/api/v1/admins/list",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestAdminAdd:
    def test_add_admin_success(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        mock_db = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admins/add",
            json={"username": "newadmin", "password": "secret123", "full_name": "New Admin"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 201

    def test_add_admin_duplicate_username(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        existing = _make_admin_mock(1, "existing")

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = existing
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admins/add",
            json={"username": "existing", "password": "secret123"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 409


class TestAdminUpdate:
    def test_update_admin_success(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        admin = _make_admin_mock(1)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = admin
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/admins/1",
            json={"full_name": "Updated Name"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        assert admin.full_name == "Updated Name"

    def test_update_admin_not_found(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/admins/999",
            json={"full_name": "Nobody"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 404


class TestAdminDelete:
    def test_delete_admin_success(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        admin = _make_admin_mock(1)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = admin
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.delete(
            "/api/v1/admins/1",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        assert admin.is_active is False

    def test_delete_admin_not_found(self, valid_token):
        from backend.api.admin_mgmt import router as admin_mgmt_router
        from backend.database import get_db

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(admin_mgmt_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.delete(
            "/api/v1/admins/999",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 404
