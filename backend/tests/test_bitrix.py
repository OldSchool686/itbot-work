import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


def _build_app(mock_svc):
    """Build a test app with mocked Bitrix24 service via dependency override."""
    from backend.api.bitrix import router as bitrix_router, _check_internal_api_key
    from backend.services.bitrix_service import get_bitrix_service

    app = FastAPI()
    app.include_router(bitrix_router)

    def override_get_svc():
        return mock_svc

    def override_api_key():
        pass  # Skip API key check in tests

    app.dependency_overrides[get_bitrix_service] = override_get_svc
    app.dependency_overrides[_check_internal_api_key] = override_api_key
    return TestClient(app), mock_svc


class TestCreateDeal:
    def test_create_deal_success(self):
        """Test successful deal creation with contact lookup."""
        from backend.utils.config import settings
        mock_svc = AsyncMock()
        mock_svc.create_contact.return_value = 42
        mock_svc.create_deal.return_value = 100

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal", json={
            "full_name": "Ivanov Ivan",
            "phone": "+79001234567",
            "department": "IT Support",
            "category": "Hardware",
            "description": "Monitor not working",
            "ticket_id": 1,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["bitrix_deal_id"] == 100
        assert data["contact_id"] == 42
        assert data["status"] == settings.bitrix24_stage_new

    def test_create_deal_payload_format(self):
        """Verify create_deal is called with correct payload per spec."""
        from backend.utils.config import settings
        mock_svc = AsyncMock()
        mock_svc.create_contact.return_value = 42
        mock_svc.create_deal.return_value = 100

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal", json={
            "full_name": "Petrov Petr",
            "phone": "+79009876543",
            "department": "Finance",
            "category": "Software",
            "description": "Excel crashes on startup",
            "ticket_id": 2,
        })

        assert resp.status_code == 200

        # Verify create_contact was called with correct args
        mock_svc.create_contact.assert_called_once_with("Petrov Petr", "+79009876543")

        # Verify create_deal payload contains all required custom fields
        call_kwargs = mock_svc.create_deal.call_args[1]
        assert call_kwargs["phone"] == "+79009876543"
        assert call_kwargs["department"] == "Finance"
        assert call_kwargs["category"] == "Software"
        assert call_kwargs["ticket_id"] == 2
        assert call_kwargs["stage_id"] == settings.bitrix24_stage_new

    def test_create_deal_no_contact_still_creates_deal(self):
        """Deal should be created even if contact creation fails."""
        mock_svc = AsyncMock()
        mock_svc.create_contact.return_value = None
        mock_svc.create_deal.return_value = 101

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal", json={
            "full_name": "Test User",
            "phone": "+79005555555",
            "department": "HR",
            "category": "Network",
            "description": "Cannot connect to WiFi",
            "ticket_id": 3,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["bitrix_deal_id"] == 101
        assert data["contact_id"] is None

    def test_create_deal_bitrix_error(self):
        """Return 502 when Bitrix24 deal creation fails."""
        mock_svc = AsyncMock()
        mock_svc.create_contact.return_value = 42
        mock_svc.create_deal.return_value = None

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal", json={
            "full_name": "Test User",
            "phone": "+79005555555",
            "department": "HR",
            "category": "Network",
            "description": "Cannot connect to WiFi",
            "ticket_id": 3,
        })

        assert resp.status_code == 502

    def test_create_deal_missing_fields(self):
        """Return 422 for missing required fields."""
        mock_svc = AsyncMock()
        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal", json={
            "full_name": "Test User",
            # Missing phone, department, category, description, ticket_id
        })

        assert resp.status_code == 422


class TestBitrixServiceDirect:
    @pytest.mark.asyncio
    async def test_create_deal_fields_include_custom_fields(self):
        """Verify the service builds deal payload with all custom fields."""
        from backend.services.bitrix_service import BitrixService

        webhook_url = "https://test.bitrix24.com/rest/1/abc/"
        mock_response_data = {"success": True, "result": 999}

        mock_resp_instance = MagicMock()
        mock_resp_instance.raise_for_status.return_value = None
        mock_resp_instance.json.return_value = mock_response_data

        with patch("backend.services.bitrix_service.settings") as mock_settings:
            mock_settings.bitrix24_webhook_url = webhook_url
            mock_settings.bitrix24_deal_type_id = "SERVICE"

            captured_call = {}

            async def fake_post(*args, **kwargs):
                captured_call["url"] = args[0] if args else None
                captured_call["json"] = kwargs.get("json", {})
                return mock_resp_instance

            with patch("httpx.AsyncClient.post", new=fake_post):
                svc = BitrixService()
                deal_id = await svc.create_deal(
                    title="Test Deal",
                    stage_id="NEW",
                    contact_id=42,
                    phone="+79001234567",
                    department="IT",
                    category="Hardware",
                    ticket_id=1,
                    description="Monitor broken",
                )

                assert deal_id == 999

                # Verify the request payload contains all custom fields
                params = captured_call["json"]
                fields = params["fields"]
                assert fields["UF_CRM_IT_PHONE"] == "+79001234567"
                assert fields["UF_CRM_IT_DEPARTMENT"] == "IT"
                assert fields["UF_CRM_IT_CATEGORY"] == "Hardware"
                assert fields["UF_CRM_IT_BOT_ID"] == 1
                assert fields["CONTACT_IDS"] == [42]

    @pytest.mark.asyncio
    async def test_get_deal_by_bot_id(self):
        """Verify get_deal_by_bot_id returns deal or None."""
        from backend.services.bitrix_service import BitrixService

        mock_resp_instance = MagicMock()
        mock_resp_instance.raise_for_status.return_value = None
        mock_resp_instance.json.return_value = {
            "success": True,
            "result": [{"ID": 100, "TITLE": "Test", "STATUS_ID": "NEW"}],
        }

        with patch("backend.services.bitrix_service.settings") as mock_settings:
            mock_settings.bitrix24_webhook_url = "https://test.bitrix24.com/rest/1/abc/"

            async def fake_post(*args, **kwargs):
                return mock_resp_instance

            with patch("httpx.AsyncClient.post", new=fake_post):
                svc = BitrixService()
                deal = await svc.get_deal_by_bot_id(1)
                assert deal is not None
                assert deal["ID"] == 100

    @pytest.mark.asyncio
    async def test_add_comment(self):
        """Verify add_comment calls correct API method."""
        from backend.services.bitrix_service import BitrixService

        mock_resp_instance = MagicMock()
        mock_resp_instance.raise_for_status.return_value = None
        mock_resp_instance.json.return_value = {"success": True, "result": True}

        with patch("backend.services.bitrix_service.settings") as mock_settings:
            mock_settings.bitrix24_webhook_url = "https://test.bitrix24.com/rest/1/abc/"

            async def fake_post(*args, **kwargs):
                return mock_resp_instance

            with patch("httpx.AsyncClient.post", new=fake_post):
                svc = BitrixService()
                await svc.add_comment(100, "Ticket resolved")

    @pytest.mark.asyncio
    async def test_update_stage(self):
        """Verify update_stage calls correct API method."""
        from backend.services.bitrix_service import BitrixService

        mock_resp_instance = MagicMock()
        mock_resp_instance.raise_for_status.return_value = None
        mock_resp_instance.json.return_value = {"success": True, "result": True}

        with patch("backend.services.bitrix_service.settings") as mock_settings:
            mock_settings.bitrix24_webhook_url = "https://test.bitrix24.com/rest/1/abc/"

            async def fake_post(*args, **kwargs):
                return mock_resp_instance

            with patch("httpx.AsyncClient.post", new=fake_post):
                svc = BitrixService()
                await svc.update_stage(100, "IN_PROGRESS")


class TestUpdateDealStage:
    def test_update_stage_success(self):
        """Test successful stage update."""
        mock_svc = AsyncMock()
        mock_svc.get_deal_by_bot_id.return_value = {"ID": 100, "TITLE": "Test", "STATUS_ID": "NEW"}

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal/100/stage", json={"stage_id": "IN_PROGRESS"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["deal_id"] == 100
        assert data["stage_id"] == "IN_PROGRESS"

    def test_update_stage_not_found(self):
        """Return 404 when deal is not found."""
        mock_svc = AsyncMock()
        mock_svc.get_deal_by_bot_id.return_value = None

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal/999/stage", json={"stage_id": "IN_PROGRESS"})
        assert resp.status_code == 404


class TestAddComment:
    def test_add_comment_success(self):
        """Test successful comment addition."""
        mock_svc = AsyncMock()

        client, _ = _build_app(mock_svc)

        resp = client.post("/api/v1/bitrix/deal/100/comment", json={"message": "Test comment"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["deal_id"] == 100
        assert data["comment_added"] is True


class TestGetDealByTicket:
    def test_get_deal_found(self):
        """Test successful deal lookup by ticket ID."""
        mock_svc = AsyncMock()
        mock_svc.get_deal_by_bot_id.return_value = {"ID": 100, "TITLE": "Test", "STATUS_ID": "NEW"}

        client, _ = _build_app(mock_svc)

        resp = client.get("/api/v1/bitrix/deal/by-ticket/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deal"]["ID"] == 100

    def test_get_deal_not_found(self):
        """Return 404 when no deal found for ticket."""
        mock_svc = AsyncMock()
        mock_svc.get_deal_by_bot_id.return_value = None

        client, _ = _build_app(mock_svc)

        resp = client.get("/api/v1/bitrix/deal/by-ticket/999")
        assert resp.status_code == 404
