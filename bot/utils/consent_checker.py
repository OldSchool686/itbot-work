import logging

import httpx

logger = logging.getLogger(__name__)


class ConsentChecker:
    """Manage user consent via backend API."""

    def __init__(self, backend_url: str = "http://localhost:8000"):
        self._backend_url = backend_url.rstrip("/")

    async def give_consent(self, phone: str) -> bool:
        """Mark consent as given for a user by phone number.

        This updates both users and allowed_users tables in the backend.
        Calls the internal bot endpoint that handles this.
        """
        try:
            import os

            headers = {}
            api_key = os.getenv("INTERNAL_API_KEY")
            if api_key:
                headers["X-Internal-Token"] = api_key
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.post(
                    f"{self._backend_url}/api/v1/bot/consent-give",
                    json={"phone": phone},
                )
                if resp.status_code != 200:
                    logger.error(f"Consent API returned {resp.status_code} for phone {phone}: {resp.text}")
                return resp.status_code == 200
        except httpx.RequestError as e:
            logger.error(f"Failed to record consent for phone {phone}: {e}")
            return False


# Module-level singleton
_consent_checker: "ConsentChecker | None" = None


def get_consent_checker() -> ConsentChecker:
    """Get or create the consent checker instance."""
    global _consent_checker
    if _consent_checker is None:
        import os
        backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
        _consent_checker = ConsentChecker(backend_url)
    return _consent_checker
