import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

_mock_db = MagicMock()
_mock_db.engine = MagicMock()
_mock_db.async_session_factory = MagicMock()


async def _mock_get_db():
    session = MagicMock()
    yield session


_mock_db.get_db = _mock_get_db
sys.modules["backend.database"] = _mock_db

_mock_chromadb = MagicMock()
sys.modules["chromadb"] = _mock_chromadb

_mock_redis_base = MagicMock()
_mock_redis_asyncio = MagicMock()
sys.modules["redis"] = _mock_redis_base
sys.modules["redis.asyncio"] = _mock_redis_asyncio


# Mock the new redis_pool module for tests
class AsyncRedisMock:
    """Async mock that returns awaitable results for Redis commands."""

    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        return True

    async def ping(self, *args, **kwargs):
        return True

    async def aclose(self, *args, **kwargs):
        pass

    # Pipeline support for rate limiter
    def pipeline(self):
        pipe = AsyncMock()
        pipe.zremrangebyscore = MagicMock(return_value=pipe)
        pipe.zcard = MagicMock(return_value=pipe)
        pipe.zadd = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        async def execute():
            return [0, 0, {}, True]
        pipe.execute = execute
        return pipe


_redis_singleton = AsyncRedisMock()


async def _mock_get_redis():
    """Return the same mock instance for all test calls."""
    return _redis_singleton


_mock_redis_pool = MagicMock()
_mock_redis_pool.get_redis = _mock_get_redis
_mock_redis_pool.close_redis = AsyncMock()
sys.modules["backend.utils.redis_pool"] = _mock_redis_pool
