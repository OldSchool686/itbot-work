from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.models.allowed_user import AllowedUser
from backend.models.ticket import Ticket


def _make_allowed_user_mock(
    id_: int = 1,
    phone: str = "+79001234567",
    full_name: str = "Ivanov Ivan",
    department: str = "IT",
    consent_given: bool = True,
    is_active: bool = True,
):
    user = MagicMock(spec=AllowedUser)
    user.id = id_
    user.phone = phone
    user.full_name = full_name
    user.department = department
    user.consent_given = consent_given
    user.consent_timestamp = datetime.now(timezone.utc) if consent_given else None
    user.is_active = is_active
    user.added_by = "admin"
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def _make_ticket_mock(
    id_: int = 1,
    status: str = "new",
    bitrix_deal_id=None,
    phone: str = "+79001234567",
):
    ticket = MagicMock(spec=Ticket)
    ticket.id = id_
    ticket.status = status
    ticket.bitrix_deal_id = bitrix_deal_id
    ticket.phone = phone
    ticket.updated_at = datetime.now(timezone.utc)
    return ticket


def _build_app(db_mock):
    from backend.api.bot_internal import router as bot_router, _check_internal_api_key
    from backend.database import get_db

    app = FastAPI()
    app.include_router(bot_router)

    async def override_get_db():
        yield db_mock

    def override_api_key():
        pass  # Skip API key check in tests

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_check_internal_api_key] = override_api_key
    return TestClient(app)


# --- check-access tests ---


class TestCheckAccess:
    def test_check_access_whitelisted_active_user(self):
        user = _make_allowed_user_mock(is_active=True, consent_given=True)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["consent_given"] is True
        assert data["user_data"]["full_name"] == "Ivanov Ivan"

    def test_check_access_whitelisted_no_consent(self):
        user = _make_allowed_user_mock(is_active=True, consent_given=False)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        assert data["consent_given"] is False

    def test_check_access_non_whitelisted(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79009999999"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False
        assert data["user_data"] is None
        assert data["consent_given"] is False

    def test_check_access_deactivated_user(self):
        user = _make_allowed_user_mock(is_active=False)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "+79001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is False

    def test_check_access_phone_normalization_8_prefix(self):
        user = _make_allowed_user_mock(phone="+79001234567", is_active=True)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post("/api/v1/bot/check-access", json={"phone": "89001234567"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True


# --- user-by-phone tests ---


class TestUserByPhone:
    def test_user_by_phone_existing(self):
        user = _make_allowed_user_mock(department="IT", consent_given=True)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/user-by-phone?phone=+79001234567")
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] == "Ivanov Ivan"
        assert data["department"] == "IT"
        assert data["consent_given"] is True

    def test_user_by_phone_not_found(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/user-by-phone?phone=+79009999999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] is None
        assert data["department"] is None
        assert data["consent_given"] is False

    def test_user_by_phone_normalization(self):
        user = _make_allowed_user_mock(phone="+79001234567")

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = user
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/user-by-phone?phone=89001234567")
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] == "Ivanov Ivan"


# --- ticket-status tests ---


class TestTicketStatus:
    def test_ticket_status_found(self):
        ticket = _make_ticket_mock(id_=1, status="in_progress", bitrix_deal_id=42)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = ticket
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/ticket-status/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"
        assert data["bitrix_deal_id"] == 42
        assert "updated_at" in data

    def test_ticket_status_not_found(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/ticket-status/999")
        assert resp.status_code == 404

    def test_ticket_status_no_bitrix_deal(self):
        ticket = _make_ticket_mock(id_=1, status="new", bitrix_deal_id=None)

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = ticket
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.get("/api/v1/bot/ticket-status/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bitrix_deal_id"] is None


# --- notify-user tests ---


class TestNotifyUser:
    def test_notify_user_success(self):
        ticket = _make_ticket_mock(id_=1, phone="+79001234567")

        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = ticket
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post(
            "/api/v1/bot/notify-user",
            json={"ticket_id": 1, "message": "Your ticket has been resolved"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticket_id"] == 1
        assert data["user_phone"] == "+79001234567"

    def test_notify_user_ticket_not_found(self):
        async def execute_side_effect(stmt):
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        mock_db = AsyncMock()
        mock_db.execute = execute_side_effect
        client = _build_app(mock_db)

        resp = client.post(
            "/api/v1/bot/notify-user",
            json={"ticket_id": 999, "message": "Hello"},
        )
        assert resp.status_code == 404
