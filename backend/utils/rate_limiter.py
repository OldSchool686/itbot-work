import asyncio
import logging
from functools import wraps
from typing import Callable, Any

from fastapi import Request, HTTPException, status
from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)


class RateLimiter:
    """Redis-based sliding window rate limiter.

    Uses Redis sorted sets to track request timestamps per key.
    Supports configurable limits and windows.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def is_allowed(self, key: str) -> bool:
        """Check if a request is allowed under the rate limit.

        Returns True if within limits, False if exceeded.
        Uses sliding window algorithm with Redis sorted sets.
        """
        try:
            r = await get_redis()
            now = asyncio.get_event_loop().time()
            window_start = now - self.window_seconds

            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, self.window_seconds + 1)
            results = await pipe.execute()

            current_count = results[1]
            return current_count < self.max_requests
        except Exception:
            logger.exception(f"Rate limiter check failed for key {key}")
            return True


_internal_rate_limiter = RateLimiter(max_requests=60, window_seconds=60)


async def check_rate_limit(request: Request, identifier: str) -> None:
    """Check rate limit for internal API requests.

    Raises HTTPException(429) if limit exceeded.
    Uses Redis sliding window for accurate counting.
    """
    key = f"rate_limit:{identifier}"
    allowed = await _internal_rate_limiter.is_allowed(key)
    
    if not allowed:
        logger.warning(f"Rate limit exceeded for {identifier}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Internal API rate limit: 60 req/min",
        )


def rate_limit_by_ip():
    """FastAPI dependency that rate limits by client IP."""
    async def dependency(request: Request):
        ip = request.client.host if request.client else "unknown"
        await check_rate_limit(request, f"ip:{ip}")
    return dependency


def rate_limit_by_user(user_id_field: str = "user_id"):
    """FastAPI dependency that rate limits by user ID from request body."""
    async def dependency(request: Request):
        body = await request.json() if request.body else {}
        user_id = body.get(user_id_field, "anonymous")
        await check_rate_limit(request, f"user:{user_id}")
    return dependency


# Admin panel rate limiter — stricter to prevent brute-force JWT attacks
_admin_rate_limiter = RateLimiter(max_requests=30, window_seconds=60)


async def admin_rate_limit_dependency(request: Request) -> None:
    """Rate limit for admin panel endpoints (30 req/min per IP)."""
    ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:admin:{ip}"
    allowed = await _admin_rate_limiter.is_allowed(key)

    if not allowed:
        logger.warning(f"Admin rate limit exceeded for {ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Admin panel rate limit: 30 req/min",
        )


# Public download endpoint rate limiter
_download_rate_limiter = RateLimiter(max_requests=20, window_seconds=60)


async def download_rate_limit_dependency(request: Request) -> None:
    """Rate limit for public template download endpoints (20 req/min per IP)."""
    ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:download:{ip}"
    allowed = await _download_rate_limiter.is_allowed(key)

    if not allowed:
        logger.warning(f"Download rate limit exceeded for {ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Download rate limit: 20 req/min",
        )
