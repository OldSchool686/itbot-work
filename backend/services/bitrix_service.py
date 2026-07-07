import logging
from typing import Optional

import httpx
from backend.utils.config import settings

logger = logging.getLogger(__name__)


class BitrixService:
    """Bitrix24 REST API client for CRM operations."""

    def __init__(self):
        self._webhook_url = settings.bitrix24_webhook_url.rstrip("/")
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        self._client = httpx.AsyncClient(timeout=30.0, limits=limits)

    async def _request(self, method: str, params: dict) -> dict:
        """Make a Bitrix24 REST API request."""
        url = f"{self._webhook_url}/{method}"
        resp = await self._client.post(url, json=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", True):
            error = data.get("error", "Unknown error")
            logger.error(f"Bitrix24 API error ({method}): {error}")
            raise RuntimeError(f"Bitrix24 API error: {error}")
        return data

    async def close(self):
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def create_contact(self, full_name: str, phone: str) -> Optional[int]:
        """Find or create a contact in Bitrix24 CRM. Returns contact_id."""
        # Try to find existing contact by phone
        try:
            result = await self._request("crm.contact.list", {
                "filter": {"PHONES_VALUE": phone},
                "select": ["ID"],
                "limit": 1,
            })
            items = result.get("result", [])
            if items:
                return items[0]["ID"]
        except RuntimeError:
            logger.warning(f"Failed to search contact by phone {phone}")

        # Create new contact
        try:
            result = await self._request("crm.contact.add", {
                "fields": {
                    "NAME": full_name.split()[0] if full_name else "",
                    "LAST_NAME": " ".join(full_name.split()[1:]) if len(full_name.split()) > 1 else "",
                    "PHONES": [{"VALUE": phone, "VALUE_TYPE": "WORK", "TYPE_ID": "MOBILE"}],
                },
            })
            return result.get("result")
        except RuntimeError as e:
            logger.error(f"Failed to create contact for {full_name}: {e}")
            return None

    async def create_deal(
        self,
        title: str,
        stage_id: str,
        contact_id: Optional[int],
        phone: str,
        department: str,
        category: str,
        ticket_id: int,
        description: str,
        full_name: str = "",
    ) -> Optional[int]:
        """Create a deal in Bitrix24 CRM. Returns deal_id."""
        fields = {
            "TITLE": title,
            "STAGE_ID": stage_id,
            "TYPE_ID": settings.bitrix24_deal_type_id,
            "COMMENTS": f"Заявка от {full_name}\n\n{description}",
            "SOURCE_ID": "MAX_BOT",
            "OPENED": "Y",
            "UF_CRM_IT_PHONE": phone,
            "UF_CRM_IT_DEPARTMENT": department,
            "UF_CRM_IT_CATEGORY": category,
            "UF_CRM_IT_BOT_ID": ticket_id,
        }
        if contact_id:
            fields["CONTACT_IDS"] = [contact_id]

        try:
            result = await self._request("crm.deal.add", {"fields": fields})
            return result.get("result")
        except RuntimeError as e:
            logger.error(f"Failed to create deal for ticket {ticket_id}: {e}")
            return None

    async def update_stage(self, deal_id: int, stage_id: str):
        """Update deal stage in Bitrix24 CRM."""
        await self._request("crm.deal.stage.update", {
            "deal_id": deal_id,
            "stage_id": stage_id,
        })

    async def get_deal_by_bot_id(self, ticket_id: int) -> Optional[dict]:
        """Find a deal by our internal bot ticket ID (UF_CRM_IT_BOT_ID)."""
        try:
            result = await self._request("crm.deal.list", {
                "filter": {"UF_CRM_IT_BOT_ID": ticket_id},
                "select": ["ID", "TITLE", "STATUS_ID"],
                "limit": 1,
            })
            items = result.get("result", [])
            return items[0] if items else None
        except RuntimeError:
            return None

    async def add_comment(self, deal_id: int, message: str):
        """Add a comment to a deal timeline."""
        await self._request("crm.deal.timeline.add", {
            "deal_id": deal_id,
            "provider_name": "bitrix24_mail_message",
            "fields": {"MESSAGE": message},
        })


# Module-level singleton
_bitrix_service: Optional[BitrixService] = None


def get_bitrix_service() -> BitrixService:
    """Get or create the Bitrix service instance."""
    global _bitrix_service
    if _bitrix_service is None:
        _bitrix_service = BitrixService()
    return _bitrix_service
