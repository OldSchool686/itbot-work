import logging

import httpx

logger = logging.getLogger(__name__)


class WhitelistChecker:
    """Check user access via backend API."""

    def __init__(self, backend_url: str = "http://localhost:8000"):
        self._backend_url = backend_url.rstrip("/")

    async def check(self, phone: str = None, max_user_id: int = None) -> dict:
        """Check if user is in whitelist. Supports lookup by phone or MAX user ID."""
        payload = {}
        if phone:
            normalized = self.normalize_phone(phone)
            payload["phone"] = normalized
        if max_user_id:
            payload["max_user_id"] = max_user_id

        try:
            import os

            headers = {}
            api_key = os.getenv("INTERNAL_API_KEY")
            if api_key:
                headers["X-Internal-Token"] = api_key
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.post(
                    f"{self._backend_url}/api/v1/bot/check-access",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f"Whitelist check failed for {payload}: {e}")
            return {"allowed": False, "user_data": None, "consent_given": False}

    async def link_user(self, phone: str, max_user_id: int) -> bool:
        """Link a MAX user ID to an allowed_users record by phone."""
        normalized = self.normalize_phone(phone)
        try:
            import os

            headers = {}
            api_key = os.getenv("INTERNAL_API_KEY")
            if api_key:
                headers["X-Internal-Token"] = api_key
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.post(
                    f"{self._backend_url}/api/v1/bot/link-user",
                    json={"phone": normalized, "max_user_id": max_user_id},
                )
                return resp.status_code == 200
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f"Failed to link user {max_user_id} to phone {normalized}: {e}")
            return False

    async def save_user(self, max_user_id: int, first_name: str = None, last_name: str = None) -> dict:
        """Save or update MAX user profile data. Returns {is_new: bool, ...}."""
        payload = {"max_user_id": max_user_id}
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name
        try:
            import os

            headers = {}
            api_key = os.getenv("INTERNAL_API_KEY")
            if api_key:
                headers["X-Internal-Token"] = api_key
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.post(
                    f"{self._backend_url}/api/v1/bot/save-user",
                    json=payload,
                )
                return resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            logger.error(f"Failed to save user {max_user_id}: {e}")
            return {"is_new": False, "error": True}

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Normalize phone to +7XXXXXXXXXX format."""
        import re

        digits = re.sub(r"\D", "", phone.strip())
        if len(digits) == 10 and digits[0] == "9":
            digits = "+7" + digits
        elif len(digits) == 11 and digits[0] == "8":
            digits = "+7" + digits[1:]
        elif len(digits) == 11 and digits[0] == "7" and not phone.startswith("+"):
            digits = "+" + digits
        elif len(digits) == 10:
            digits = "+79" + digits[1:]
        return digits if digits.startswith("+") else f"+{digits}"


# Module-level singleton (lazy init)
_whitelist_checker: "WhitelistChecker | None" = None


def get_whitelist_checker() -> WhitelistChecker:
    """Get or create the whitelist checker instance."""
    global _whitelist_checker
    if _whitelist_checker is None:
        import os
        backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
        _whitelist_checker = WhitelistChecker(backend_url)
    return _whitelist_checker
