"""Admin panel HTML page endpoints."""
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter(prefix="/admin", tags=["admin panel"])


_templates_dir = os.path.join(os.path.dirname(__file__), "..", "admin_panel", "templates")
_templates = Jinja2Templates(directory=_templates_dir)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Admin login page."""
    return _templates.TemplateResponse("login.html", {"request": request})


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Main admin dashboard with tabs for documents, users, admins."""
    return _templates.TemplateResponse("index.html", {"request": request})


@router.get("/documents", response_class=HTMLResponse)
async def documents_page(request: Request):
    """Documents management page."""
    return _templates.TemplateResponse("documents.html", {"request": request})


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """Users whitelist management page."""
    return _templates.TemplateResponse("users.html", {"request": request})


@router.get("/import-csv", response_class=HTMLResponse)
async def import_csv_page(request: Request):
    """CSV user import page."""
    return _templates.TemplateResponse("user_import.html", {"request": request})


@router.get("/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request):
    """Ticket management page."""
    return _templates.TemplateResponse("tickets.html", {"request": request})


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """Analytics dashboard page."""
    return _templates.TemplateResponse("analytics.html", {"request": request})


# --- Protected service links (docs/redoc/openapi.json) ---

from fastapi.openapi.utils import get_openapi


@router.get("/openapi.json")
async def openapi_json():
    """Protected OpenAPI schema endpoint."""
    from backend.main import app as main_app

    return JSONResponse(get_openapi(
        title=main_app.title,
        version=main_app.version,
        routes=main_app.routes,
    ))


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    """Protected Swagger UI documentation."""
    return _templates.TemplateResponse("swagger.html", {"request": request})


@router.get("/redoc", response_class=HTMLResponse)
async def redoc_page(request: Request):
    """Protected ReDoc documentation."""
    return _templates.TemplateResponse("redoc.html", {"request": request})
