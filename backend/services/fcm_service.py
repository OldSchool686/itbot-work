"""Firebase Cloud Messaging service — push notifications for mobile app."""
import logging

logger = logging.getLogger(__name__)


async def send_fcm_notification(token: str, title: str, body: str) -> dict:
    """Send a push notification to a single FCM device token.

    Returns {"success": bool, "error": str | None}.
    Currently returns placeholder — requires Firebase Admin SDK setup.
    """
    from backend.utils.config import settings

    if not settings.fcm_project_id or not settings.fcm_cred_json:
        logger.warning("FCM not configured (fcm_project_id/cred_json missing)")
        return {"success": False, "error": "FCM not configured"}

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        cred = credentials.Certificate(settings.fcm_cred_json)
        if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:  # noqa: SLF041
            firebase_admin.initialize_app(cred)

        response = messaging.send(
            messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
            )
        )
        return {"success": True, "error": None}
    except Exception as e:
        logger.exception("FCM send failed for token %s...", token[:8])
        return {"success": False, "error": str(e)}
