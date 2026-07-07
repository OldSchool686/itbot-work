import json
import logging
from typing import Optional, Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class FSMStateStorage:
    """Redis-backed finite state machine storage for bot sessions."""

    SESSION_TTL = 600  # 10 minutes

    def __init__(self, redis_url: str):
        self._redis = None
        self._redis_url = redis_url
        self._ttl = self.SESSION_TTL

    async def connect(self):
        """Connect to Redis."""
        self._redis = aioredis.from_url(
            self._redis_url, decode_responses=True
        )

    async def disconnect(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.aclose()

    @staticmethod
    def _make_key(max_user_id: int, chat_id: str) -> str:
        return f"session:{max_user_id}:{chat_id}"

    async def get_state(self, max_user_id: int, chat_id: str) -> Optional[str]:
        """Get current FSM state for a user session."""
        key = self._make_key(max_user_id, chat_id) + ":state"
        state = await self._redis.get(key)
        if state:
            await self._redis.expire(key, self._ttl)  # auto-renew TTL
        return state

    async def set_state(self, max_user_id: int, chat_id: str, state: str):
        key = self._make_key(max_user_id, chat_id) + ":state"
        await self._redis.set(key, state, ex=self._ttl)

    async def delete_state(self, max_user_id: int, chat_id: str):
        """Clear FSM state for a user session."""
        base = self._make_key(max_user_id, chat_id)
        await self._redis.delete(f"{base}:state", f"{base}:data")

    async def get_data(self, max_user_id: int, chat_id: str) -> dict[str, Any]:
        """Get session data (arbitrary JSON)."""
        key = self._make_key(max_user_id, chat_id) + ":data"
        raw = await self._redis.get(key)
        if raw:
            await self._redis.expire(key, self._ttl)  # auto-renew TTL
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Corrupted session data for {max_user_id}:{chat_id}, clearing")
                await self._redis.delete(key)
        return {}

    async def set_data(self, max_user_id: int, chat_id: str, data: dict[str, Any]):
        """Set session data."""
        key = self._make_key(max_user_id, chat_id)
        await self._redis.set(f"{key}:data", json.dumps(data), ex=self._ttl)

    async def check_cooldown(self, max_user_id: int, action: str, seconds: int) -> bool:
        """Check if user is within cooldown for an action. Returns True if cooled down (OK to proceed)."""
        key = f"cooldown:{max_user_id}:{action}"
        last_ts = await self._redis.get(key)
        if last_ts is None:
            return True
        import time
        elapsed = time.time() - float(last_ts)
        if elapsed >= seconds:
            return True
        return False

    async def set_cooldown(self, max_user_id: int, action: str, seconds: int):
        """Set cooldown timestamp for an action."""
        key = f"cooldown:{max_user_id}:{action}"
        import time
        await self._redis.set(key, str(time.time()), ex=seconds + 60)


# Module-level singleton (initialized in main.py)
_fsm_instance: Optional[FSMStateStorage] = None


def get_storage() -> FSMStateStorage:
    """Get the initialized storage instance. Call after main() starts."""
    if _fsm_instance is None:
        raise RuntimeError("FSMStateStorage not initialized — call from bot/main.py first")
    return _fsm_instance


async def init_storage(redis_url: str) -> FSMStateStorage:
    """Initialize and connect the storage singleton. Call once at startup."""
    global _fsm_instance
    _fsm_instance = FSMStateStorage(redis_url)
    await _fsm_instance.connect()
    return _fsm_instance


async def shutdown_storage():
    """Disconnect the storage singleton."""
    global _fsm_instance
    if _fsm_instance:
        await _fsm_instance.disconnect()
        _fsm_instance = None
