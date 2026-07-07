"""Admin panel auth middleware — server-side JWT check for /admin routes."""
import re
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# Routes that serve HTML pages (need redirect to login)
_HTML_PATHS = {"/", "/documents", "/users", "/tickets", "/analytics", "/import-csv"}

# Routes that return JSON/API data (should 401, not redirect)
_JSON_RE = re.compile(r"^/(openapi\.json|docs|redoc)$")


def _is_admin_html_path(path: str) -> bool:
    """Check if path is an admin HTML page."""
    for hp in _HTML_PATHS:
        if path == hp or path.startswith(hp + "/"):
            return True
    # Check if it's a JSON/API endpoint under /admin/
    if not _JSON_RE.match(path):
        return False
    return False


def _is_admin_json_path(path: str) -> bool:
    """Check if path is an admin API/docs endpoint."""
    return bool(_JSON_RE.match(path))


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Verify JWT token for all /admin routes except /login.

    - HTML pages → redirect to /admin/login with ?next= query param
    - JSON/API endpoints → 401 Unauthorized
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        from backend.utils.auth_jwt import decode_access_token

        self.decode_access_token = decode_access_token

    async def dispatch(self, request: Request, call_next):
        full_path = str(request.url.path)

        # Skip non-admin routes and static files
        if not full_path.startswith("/admin"):
            return await call_next(request)

        path = full_path.removeprefix("/admin") or "/"

        # Allow login page unconditionally
        if path == "/login":
            return await call_next(request)

        # Extract token from cookie or Authorization header
        token = request.cookies.get("admin_token")
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        if not token:
            # No token — redirect HTML, 401 for JSON
            if _is_admin_html_path(path):
                next_url = path + str(request.url.query) or ""
                return RedirectResponse(
                    url=f"/admin/login?next={request.url.path}", status_code=303
                )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        # Validate token
        username = self.decode_access_token(token)
        if not username:
            # Invalid/expired token — redirect to login
            return RedirectResponse(url="/admin/login", status_code=303)

        return await call_next(request)
