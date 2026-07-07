import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from backend.utils.config import settings
from backend.database import async_session_factory
from backend.models.admin import Admin
from backend.models.allowed_user import AllowedUser
from backend.api.auth import router as auth_router, hash_password
from backend.api.admin_mgmt import router as admin_mgmt_router
from backend.api.users import router as users_router
from backend.api.bot_internal import router as bot_internal_router
from backend.api.bitrix import router as bitrix_router
from backend.api.rag import router as rag_router
from backend.api.documents import router as documents_router, department_router
from backend.api.templates import router as templates_router
from backend.api.tickets import router as tickets_router
from backend.api.admin_panel import router as admin_panel_router
from backend.api.attachments import router as attachments_router
from backend.api.mobile import router as mobile_router
from backend.utils.timing_middleware import TimingMiddleware
from backend.utils.security_headers import SecurityHeadersMiddleware
from backend.utils.admin_auth_middleware import AdminAuthMiddleware


class GlobalRateLimitMiddleware:
    """Apply rate limits to /admin/ and /api/v1/auth/ routes.

    Uses the existing Redis-based sliding window limiter (30 req/min per IP).
    Placed early in middleware stack so it blocks before auth checks run.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not (path.startswith("/admin/") or path.startswith("/api/v1/auth")):
            await self.app(scope, receive, send)
            return

        # Extract client IP from ASGI connection info
        client = scope.get("client")
        ip = client[0] if client else "unknown"

        try:
            from backend.utils.rate_limiter import _admin_rate_limiter

            key = f"rate_limit:global:{ip}"
            allowed = await _admin_rate_limiter.is_allowed(key)
            if not allowed:
                scope["method"] = "GET"
                request = Request(scope)
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Try again later."},
                )
                await response(scope, receive, send)
                return

        except Exception:
            # Fail-open: if Redis is down, let the request through
            pass

        await self.app(scope, receive, send)


logger = logging.getLogger(__name__)


async def _check_postgres() -> dict:
    """Check PostgreSQL connectivity."""
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        logger.exception("PostgreSQL health check failed")
        return {"status": "unhealthy", "error": str(e)}


async def _check_redis() -> dict:
    """Check Redis connectivity."""
    try:
        from backend.utils.redis_pool import get_redis

        r = await get_redis()
        pong = await r.ping()
        return {"status": "healthy"} if pong else {"status": "unhealthy", "error": "Redis ping failed"}
    except Exception as e:
        logger.exception("Redis health check failed")
        return {"status": "unhealthy", "error": str(e)}


async def _check_ollama() -> dict:
    """Check Ollama API connectivity."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return {"status": "healthy", "models": models}
    except Exception as e:
        logger.exception("Ollama health check failed")
    return {"status": "unhealthy", "error": str(e) if 'e' in locals() else "connection failed"}


async def _check_chromadb() -> dict:
    """Check ChromaDB connectivity."""
    try:
        import chromadb

        client = chromadb.HttpClient(host=settings.chroma_db_host, port=settings.chroma_db_port)
        client.heartbeat()
        return {"status": "healthy"}
    except Exception as e:
        logger.exception("ChromaDB health check failed")
        return {"status": "unhealthy", "error": str(e)}


async def _check_bitrix24() -> dict:
    """Check Bitrix24 webhook connectivity."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.bitrix24_webhook_url.rstrip('/')}/crm.deal.list",
                json={"limit": 1, "select": ["ID"]},
            )
            if resp.status_code == 200:
                return {"status": "healthy"}
    except Exception as e:
        logger.exception("Bitrix24 health check failed")
    return {"status": "unhealthy", "error": str(e) if 'e' in locals() else "connection failed"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Create initial admin if admins table is empty
    async with async_session_factory() as session:
        count_result = await session.execute(select(func.count(Admin.id)))
        admin_count = count_result.scalar()

        if admin_count == 0:
            initial_admin = Admin(
                username=settings.admin_initial_username,
                password_hash=hash_password(settings.admin_initial_password),
                full_name="Initial Administrator",
                is_active=True,
            )
            session.add(initial_admin)
            await session.commit()
            logger.warning(f"Initial admin created: {settings.admin_initial_username}")
            logger.warning("CHANGE THE PASSWORD IMMEDIATELY!")

        if settings.admin_initial_phone and not settings.admin_initial_phone.startswith("#"):
            from backend.api.bot_internal import normalize_phone as _normalize_phone
            norm_phone = _normalize_phone(settings.admin_initial_phone)
            result = await session.execute(select(AllowedUser).where(AllowedUser.phone == norm_phone))
            existing_user = result.scalar_one_or_none()
            if not existing_user:
                initial_allowed_user = AllowedUser(
                    phone=norm_phone,
                    full_name=settings.admin_initial_full_name or "Administrator",
                    department="IT Department",
                    consent_given=True,
                    is_active=True,
                    added_by="system",
                )
                session.add(initial_allowed_user)
                await session.commit()
                logger.warning(f"Initial allowed user seeded: {norm_phone} ({settings.admin_initial_full_name})")

    from backend.services.background_tasks import start_background_tasks, stop_background_tasks

    await start_background_tasks()
    logger.info("Backend started")
    yield
    await stop_background_tasks()
    from backend.services.ollama_client import get_ollama_client
    from backend.services.bitrix_service import get_bitrix_service
    from backend.utils.redis_pool import close_redis

    oc = get_ollama_client()
    if oc:
        await oc.close()
    bs = get_bitrix_service()
    if bs:
        await bs.close()
    await close_redis()
    logger.info("Backend shutting down")


app = FastAPI(
    title="IT Support Bot Backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Request timing middleware (must be first to wrap all requests)
app.add_middleware(TimingMiddleware)

# Global rate limiting for /admin/ and /api/v1/auth/ routes (30 req/min per IP)
app.add_middleware(GlobalRateLimitMiddleware)

# Security headers middleware — adds CSP, HSTS, X-Frame-Options, etc.
app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware for admin panel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins.split(",") if settings.allowed_cors_origins else ["http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Internal-Token",
    ],
    max_age=600,
)

# Admin panel auth middleware — server-side JWT check for /admin/* routes
app.add_middleware(AdminAuthMiddleware)


# API routers
app.include_router(auth_router)
app.include_router(admin_mgmt_router)
app.include_router(users_router)
app.include_router(tickets_router)
app.include_router(bitrix_router)
app.include_router(bot_internal_router)
app.include_router(documents_router)
app.include_router(templates_router)
app.include_router(department_router)
app.include_router(rag_router)
app.include_router(attachments_router)
app.include_router(mobile_router)

# Admin panel HTML pages
app.include_router(admin_panel_router)

# Static files for admin panel
_static_dir = os.path.join(os.path.dirname(__file__), "admin_panel", "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/api/v1/health")
async def health():
    """Detailed health check with dependency status.

    Returns status of all dependencies: PostgreSQL, Redis, Ollama, ChromaDB, Bitrix24.
    Used by Docker/Kubernetes for readiness checks.
    """
    results = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        _check_ollama(),
        _check_chromadb(),
        _check_bitrix24(),
        return_exceptions=True,
    )

    dependencies = {
        "postgres": results[0],
        "redis": results[1],
        "ollama": results[2],
        "chromadb": results[3],
        "bitrix24": results[4],
    }

    all_healthy = all(
        isinstance(r, dict) and r.get("status") == "healthy" for r in results
    )

    return {
        "status": "ok" if all_healthy else "degraded",
        "dependencies": dependencies,
    }


@app.get("/api/v1/health/live")
async def health_live():
    """Liveness probe — returns 200 if service is running.

    Used by Docker/Kubernetes for liveness checks (restart on crash).
    Does NOT check dependencies — just confirms the process is alive.
    """
    return {"status": "alive"}


@app.get("/api/v1/health/ready")
async def health_ready():
    """Readiness probe — returns 200 if all critical dependencies are healthy.

    Used by Docker/Kubernetes for readiness checks (traffic routing).
    Returns 503 if any dependency is unhealthy, triggering restart/retry.
    """
    results = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        return_exceptions=True,
    )

    critical_healthy = all(
        isinstance(r, dict) and r.get("status") == "healthy" for r in results
    )

    if not critical_healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "postgres": results[0],
                "redis": results[1],
            },
        )

    return {"status": "ready"}


@app.get("/api/v1/performance/stats")
async def performance_stats():
    """Get current request performance statistics.

    Returns average, p95, and max response times for monitoring.
    Requires valid JWT Bearer token (admin only).
    """
    from backend.utils.timing_middleware import get_request_stats
    from backend.api.documents import _get_username_from_header

    return await get_request_stats()
