from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.models.allowed_user import AllowedUser
from backend.utils.auth_jwt import create_access_token


def _make_user_mock(
    id_: int = 1,
    phone: str = "+79001234567",
    full_name: str = "Ivanov Ivan",
    department: str = "IT",
    consent_given: bool = True,
    is_active: bool = True,
    added_by: str = "admin1",
):
    user = MagicMock(spec=AllowedUser)
    user.id = id_
    user.phone = phone
    user.full_name = full_name
    user.department = department
    user.consent_given = consent_given
    user.consent_timestamp = datetime.now(timezone.utc) if consent_given else None
    user.is_active = is_active
    user.added_by = added_by
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)

    def to_dict():
        return {
            "id": id_,
            "phone": phone,
            "full_name": full_name,
            "department": department,
            "consent_given": consent_given,
            "consent_timestamp": user.consent_timestamp.isoformat() if user.consent_timestamp else None,
            "is_active": is_active,
            "added_by": added_by,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }

    user.to_dict = to_dict
    return user


@pytest.fixture
def valid_token():
    return create_access_token("admin1")


class TestPhoneNormalization:
    def test_normalize_plus7(self):
        from backend.api.users import normalize_phone
        assert normalize_phone("+79001234567") == "+79001234567"

    def test_normalize_8_prefix(self):
        from backend.api.users import normalize_phone
        assert normalize_phone("89001234567") == "+79001234567"

    def test_normalize_bare_7(self):
        from backend.api.users import normalize_phone
        assert normalize_phone("79001234567") == "+79001234567"

    def test_normalize_with_spaces_and_dashes(self):
        from backend.api.users import normalize_phone
        assert normalize_phone("+7 900-123-45-67") == "+79001234567"

    def test_service_normalize_8_prefix(self):
        from backend.services.user_importer import normalize_phone
        assert normalize_phone("89001234567") == "+79001234567"


class TestUserAdd:
    def test_add_user_valid_phone_plus7(self, valid_token):
        from backend.api.users import router as users_router
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
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/users/add",
            json={"phone": "+79001234567", "full_name": "Ivanov Ivan", "department": "IT"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["message"] == "User created"

    def test_add_user_valid_phone_8_prefix(self, valid_token):
        from backend.api.users import router as users_router
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
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/users/add",
            json={"phone": "89001234567", "full_name": "Petrov Petr"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 201

    def test_add_user_invalid_phone(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        mock_db = AsyncMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/users/add",
            json={"phone": "12345", "full_name": "Bad Phone"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 422

    def test_add_user_duplicate_phone(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        existing = _make_user_mock(1, "+79001234567")

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = existing
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/users/add",
            json={"phone": "+79001234567", "full_name": "Duplicate"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 409

    def test_add_user_unauthorized(self):
        from backend.api.users import router as users_router
        from backend.database import get_db

        mock_db = AsyncMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.post(
            "/api/v1/users/add",
            json={"phone": "+79001234567", "full_name": "No Auth"},
        )
        assert resp.status_code == 401


class TestUserList:
    def test_list_users_success(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        users = [_make_user_mock(1), _make_user_mock(2)]
        mock_db = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            raw_cols = getattr(stmt, "_raw_columns", ()) or ()
            is_count = False
            for col in raw_cols:
                if type(col).__name__.lower() == "count":
                    is_count = True
                    break

            if is_count:
                result.scalar.return_value = len(users)
            else:
                result.scalar.return_value = len(users)
                result.scalars.return_value.all.return_value = users

            return result

        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get(
            "/api/v1/users/list?page=1&per_page=50",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_list_users_unauthorized(self):
        from backend.api.users import router as users_router
        from backend.database import get_db

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get("/api/v1/users/list")
        assert resp.status_code == 401


class TestUserUpdate:
    def test_update_user_success(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        user = _make_user_mock(1)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/users/1",
            json={"full_name": "Updated Name"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        assert user.full_name == "Updated Name"

    def test_update_user_not_found(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/users/999",
            json={"full_name": "Nobody"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 404


class TestUserDelete:
    def test_delete_user_success(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        user = _make_user_mock(1)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.delete(
            "/api/v1/users/1",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "User deleted"

    def test_delete_user_not_found(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.delete(
            "/api/v1/users/999",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 404


class TestUserDeactivate:
    def test_deactivate_user_success(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        user = _make_user_mock(1)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/users/1/deactivate",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        assert user.is_active is False

    def test_deactivate_user_not_found(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.put(
            "/api/v1/users/999/deactivate",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 404


class TestCSVImport:
    def test_import_csv_upsert_mode(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        mock_db = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        csv_data = b"\xef\xbb\xbfphone,full_name,department,consent_given\n+79001234567,Ivanov Ivan,IT,true\n89009876543,Petrov Petr,HR,false"
        resp = client.post(
            "/api/v1/users/import-csv",
            files={"file": ("test.csv", csv_data, "text/csv")},
            data={"mode": "upsert"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 2
        assert data["updated"] == 0

    def test_import_csv_replace_mode(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        mock_db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt):
            call_count[0] += 1
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        csv_data = b"\xef\xbb\xbfphone,full_name,department,consent_given\n+79001234567,Ivanov Ivan,IT,true"
        resp = client.post(
            "/api/v1/users/import-csv",
            files={"file": ("test.csv", csv_data, "text/csv")},
            data={"mode": "replace"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200

    def test_import_csv_skips_invalid_rows(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        mock_db = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = execute_side_effect
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        csv_data = b"\xef\xbb\xbfphone,full_name,department,consent_given\n+79001234567,Ivanov Ivan,IT,true\ninvalid_phone,,,\n"
        resp = client.post(
            "/api/v1/users/import-csv",
            files={"file": ("test.csv", csv_data, "text/csv")},
            data={"mode": "upsert"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == 1
        assert data["skipped"] == 1


class TestCSVExport:
    def test_export_csv_success(self, valid_token):
        from backend.api.users import router as users_router
        from backend.database import get_db

        users = [_make_user_mock(1), _make_user_mock(2)]
        mock_db = AsyncMock()

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalars.return_value.all.return_value = users
            return result

        mock_db.execute = execute_side_effect

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get(
            "/api/v1/users/export-csv",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        content = resp.text
        assert "phone" in content
        assert "full_name" in content
        assert "Ivanov Ivan" in content

    def test_export_csv_unauthorized(self):
        from backend.api.users import router as users_router
        from backend.database import get_db

        app = FastAPI()
        app.include_router(users_router)

        async def override_get_db():
            yield AsyncMock()

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        resp = client.get("/api/v1/users/export-csv")
        assert resp.status_code == 401


class TestServiceLayer:
    def test_service_normalize_phone(self):
        from backend.services.user_importer import normalize_phone
        assert normalize_phone("89001234567") == "+79001234567"
        assert normalize_phone("+79001234567") == "+79001234567"
        assert normalize_phone(" 8 900-123-45-67 ") == "+79001234567"
