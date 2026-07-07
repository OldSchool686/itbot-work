"""Photo attachment handling for MAX bot messages."""
import logging
from typing import List

from bot.utils import MAX_PHOTOS_PER_TICKET

logger = logging.getLogger(__name__)

# Attachment types that contain media we care about
_MEDIA_TYPES = {"image", "video", "file"}


def extract_photo_urls(event) -> List[str]:
    """Extract photo/attachment URLs from a MAX message event.

    Incoming attachments are maxapi pydantic objects:
      Image.payload → PhotoAttachmentPayload(url, token, photo_id)
                      or OtherAttachmentPayload(url, token)
                      or PhotoAttachmentRequestPayload(url, token, photos)
    Returns up to MAX_PHOTOS_PER_TICKET URLs (or "token:..." strings as fallback).
    """
    urls = []

    msg = getattr(event, "message", None)
    if not msg:
        logger.debug("extract_photo_urls: no message on event")
        return []

    body = getattr(msg, "body", None) or msg
    attachments = getattr(body, "attachments", None) or []
    logger.debug(f"extract_photo_urls: {len(attachments)} attachments found")

    for att in (attachments or []):
        if len(urls) >= MAX_PHOTOS_PER_TICKET:
            break

        # Only process media-type attachments
        att_type = getattr(att, "type", None)
        # Handle enum objects and string values
        if hasattr(att_type, "value"):
            att_type_str = att_type.value
        else:
            att_type_str = str(att_type)

        if att_type_str not in _MEDIA_TYPES:
            logger.debug(f"extract_photo_urls: skipping non-media type={att_type_str}")
            continue

        payload = getattr(att, "payload", None)
        if payload is None:
            logger.debug(f"extract_photo_urls: attachment has no payload")
            continue

        # Try to get URL from payload (PhotoAttachmentPayload / OtherAttachmentPayload)
        url = getattr(payload, "url", None)
        token = getattr(payload, "token", None)

        if url:
            urls.append(str(url))
            logger.debug(f"extract_photo_urls: got URL {str(url)[:60]}...")
        elif token:
            urls.append(f"token:{token}")
            logger.debug(f"extract_photo_urls: got token {token[:20]}...")
        else:
            # PhotoAttachmentRequestPayload may have nested photos dict
            photos = getattr(payload, "photos", None)
            if isinstance(photos, dict):
                for photo_key, photo_obj in list(photos.items())[:MAX_PHOTOS_PER_TICKET - len(urls)]:
                    p_url = getattr(photo_obj, "url", None) or str(getattr(photo_obj, "token", ""))
                    if p_url:
                        urls.append(p_url)
            logger.debug(f"extract_photo_urls: no url/token found, payload type={type(payload).__name__}")

    return urls[:MAX_PHOTOS_PER_TICKET]


def format_photo_preview(urls: List[str]) -> str:
    """Format photo URLs for display in confirmation message."""
    if not urls:
        return ""
    lines = [f"📷 Фото ({len(urls)} шт.):"]
    for i, url in enumerate(urls, 1):
        short = (url[:60] + "...") if len(url) > 63 else url
        lines.append(f"   {i}. {short}")
    return "\n".join(lines)
