"""Attachment utility endpoints — file type probing for admin panel."""
import ipaddress
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from urllib.parse import urlparse, unquote

from backend.utils.auth_deps import require_admin_void as _require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/attachments", tags=["attachments"])


# Content-Type → (ext, icon, cssClass) mapping
_CONTENT_MAP = {
    "image/jpeg": ("jpg", "📸", "fc-image"),
    "image/png": ("png", "📸", "fc-image"),
    "image/gif": ("gif", "📸", "fc-image"),
    "image/webp": ("webp", "📸", "fc-image"),
    "image/bmp": ("bmp", "📸", "fc-image"),
    "video/mp4": ("mp4", "🎬", "fc-video"),
    "video/x-msvideo": ("avi", "🎬", "fc-video"),
    "video/quicktime": ("mov", "🎬", "fc-video"),
    "video/webm": ("webm", "🎬", "fc-video"),
    "application/pdf": ("pdf", "📄", "fc-pdf"),
    "application/msword": ("doc", "📆", "fc-doc"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx", "📆", "fc-doc"),
    "application/vnd.ms-excel": ("xls", "📋", "fc-doc"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xlsx", "📋", "fc-doc"),
    "application/vnd.ms-powerpoint": ("ppt", "📎", "fc-doc"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("pptx", "📎", "fc-doc"),
    "application/zip": ("zip", "📦", "fc-archive"),
    "application/x-rar-compressed": ("rar", "📦", "fc-archive"),
    "application/x-7z-compressed": ("7z", "📦", "fc-archive"),
    "audio/mpeg": ("mp3", "🎵", "fc-audio"),
    "audio/wav": ("wav", "🎵", "fc-audio"),
    "audio/ogg": ("ogg", "🎵", "fc-audio"),
}

# Extension → (ext, icon, cssClass) mapping for fallback when Content-Type is unknown
_EXT_MAP = {
    ".jpg": ("jpg", "📸", "fc-image"),
    ".jpeg": ("jpg", "📸", "fc-image"),
    ".png": ("png", "📸", "fc-image"),
    ".gif": ("gif", "📸", "fc-image"),
    ".webp": ("webp", "📸", "fc-image"),
    ".bmp": ("bmp", "📸", "fc-image"),
    ".mp4": ("mp4", "🎬", "fc-video"),
    ".avi": ("avi", "🎬", "fc-video"),
    ".mov": ("mov", "🎬", "fc-video"),
    ".webm": ("webm", "🎬", "fc-video"),
    ".pdf": ("pdf", "📄", "fc-pdf"),
    ".doc": ("doc", "📆", "fc-doc"),
    ".docx": ("docx", "📆", "fc-doc"),
    ".xls": ("xls", "📋", "fc-doc"),
    ".xlsx": ("xlsx", "📋", "fc-doc"),
    ".ppt": ("ppt", "📎", "fc-doc"),
    ".pptx": ("pptx", "📎", "fc-doc"),
    ".zip": ("zip", "📦", "fc-archive"),
    ".rar": ("rar", "📦", "fc-archive"),
    ".7z": ("7z", "📦", "fc-archive"),
    ".mp3": ("mp3", "🎵", "fc-audio"),
    ".wav": ("wav", "🎵", "fc-audio"),
    ".ogg": ("ogg", "🎵", "fc-audio"),
}


def _parse_content_disposition(header_value: str) -> Optional[str]:
    """Extract filename from Content-Disposition header, handling all encoding formats."""
    if not header_value or "filename" not in header_value:
        return None

    # Try filename*=UTF-8''... first (RFC 5987)
    for part in header_value.split(";"):
        part = part.strip()
        if part.startswith("filename*="):
            raw = part.split("=")[1].strip().strip('"\'')
            if "''" in raw:
                raw = raw.split("''", 1)[1]
            try:
                return unquote(raw)
            except Exception:
                pass

    for part in header_value.split(";"):
        part = part.strip()
        if part.startswith("filename="):
            name = part.split("=")[1].strip().strip('"\'')
            # Try URL-decode first (MAX may send %D0%98... without RFC 5987 prefix)
            decoded = unquote(name)
            # If decoded looks like valid UTF-8, return it
            if any(ord(c) >= 128 for c in decoded):
                return decoded
            try:
                # Try latin-1 → re-encode as UTF-8 (some servers use ISO-8859-1)
                return name.encode("latin-1").decode("utf-8")
            except Exception:
                pass
            return decoded
    return None


def _is_blocked_url(url: str) -> bool:
    """Block private/reserved IP ranges, localhost, and internal hostnames."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if not hostname:
            return True

        # Block common local/internal names
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
            return True

        # Check if it's a literal IP address in private/reserved range
        try:
            ip = ipaddress.ip_address(hostname)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            )
        except ValueError:
            pass

        # Block internal domain suffixes
        internal_suffixes = (".local", ".internal", ".lan", ".corp")
        if any(hostname.endswith(s) for s in internal_suffixes):
            return True

    except Exception:
        logger.exception(f"URL check failed for {url}")
        return True  # Fail closed on error

    return False


@router.get("/probe")
async def probe_attachment(
    url: str = Query(..., min_length=1, description="URL to probe"),
    _admin: None = Depends(_require_admin),
):
    """Perform a HEAD/GET request on a URL and return Content-Type + guessed extension."""
    if not url.startswith("http"):
        return {"ext": "", "icon": "📄", "cssClass": "fc-other", "name": ""}

    # SSRF protection — block internal/private URLs
    if _is_blocked_url(url):
        logger.warning(f"SSRF blocked: private/internal URL attempt: {url}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access to internal URLs is forbidden",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # Try HEAD first (lightweight), fall back to GET if server rejects HEAD
            resp = await client.head(url)
            if resp.status_code == 405:
                logger.debug("HEAD returned 405, trying GET")
                resp = await client.get(url)

            ct = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            cd_raw = resp.headers.get("content-disposition", "")
            fname_hint = _parse_content_disposition(cd_raw)

            # Debug: log all relevant headers from MAX
            logger.info(f"Probe debug — status={resp.status_code}, ct={ct!r}, cd={cd_raw!r}, parsed_name={fname_hint!r}")

            # Resolve display info: Content-Type → Extension fallback → default
            ext, icon, css_class = "", "📄", "fc-other"
            info = _CONTENT_MAP.get(ct)
            if not info and fname_hint:
                dot_idx = fname_hint.rfind(".")
                if dot_idx >= 0:
                    file_ext = fname_hint[dot_idx:].lower()
                    info = _EXT_MAP.get(file_ext)

            if info:
                ext, icon, css_class = info

            display_name = ""
            if fname_hint:
                dot_idx = fname_hint.rfind(".")
                display_name = fname_hint[:dot_idx] if dot_idx >= 0 else fname_hint

            logger.info(f"Probe success: ct={ct}, ext={ext}, name={display_name}")
            return {
                "ext": ext,
                "icon": icon,
                "cssClass": css_class,
                "name": display_name,
                "contentType": ct,
            }

    except Exception as e:
        logger.warning(f"Probe failed for URL: {e}")
        return {"ext": "", "icon": "📄", "cssClass": "fc-other", "name": ""}
