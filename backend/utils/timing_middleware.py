import asyncio
import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs request duration and status code.

    Logs slow requests (>500ms) as warnings for alerting.
    Skips health check endpoints to avoid noise.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        skip_paths = ["/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            return await call_next(request)

        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        log_func = logger.warning if duration_ms > 500 else logger.info
        log_func(
            f"{request.method} {request.url.path} → {response.status_code} "
            f"in {duration_ms:.0f}ms"
        )

        response.headers["X-Response-Time-Ms"] = str(f"{duration_ms:.1f}")
        return response


class SlowRequestTracker:
    """Tracks slow requests and provides metrics.

    Maintains a sliding window of request durations for monitoring.
    """

    def __init__(self, max_size: int = 1000):
        self._durations: asyncio.Queue[float] = asyncio.Queue(maxsize=max_size)
        self._lock = asyncio.Lock()

    async def record(self, path: str, duration_ms: float, status_code: int):
        """Record a request duration."""
        if self._durations.full():
            await self._durations.get()
        await self._durations.put(duration_ms)

    async def get_stats(self) -> dict:
        """Get current performance statistics."""
        items = []
        temp_queue = asyncio.Queue()
        while not self._durations.empty():
            try:
                item = self._durations.get_nowait()
                if self._durations.full():
                    await temp_queue.put(item)
                else:
                    items.append(item)
            except asyncio.QueueEmpty:
                break
        for _ in items:
            await self._durations.put(await temp_queue.get())

        if not items:
            return {"count": 0, "avg_ms": 0, "p95_ms": 0, "max_ms": 0}

        items.sort()
        p95_idx = int(len(items) * 0.95)
        return {
            "count": len(items),
            "avg_ms": round(sum(items) / len(items), 1),
            "p95_ms": round(items[min(p95_idx, len(items) - 1)], 1),
            "max_ms": round(max(items), 1),
        }


_slow_tracker = SlowRequestTracker()


async def get_request_stats() -> dict:
    """Get current request performance statistics."""
    return await _slow_tracker.get_stats()
