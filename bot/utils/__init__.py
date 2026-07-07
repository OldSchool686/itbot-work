import os


# Bot limits
MAX_PHOTOS_PER_TICKET = 3
TICKETS_LIST_LIMIT = 5

# VCF phone regex pattern (10-12 digits to support country codes)
VCF_PHONE_PATTERN = r"TEL[^:]*:(\d{10,12})"


def _internal_headers():
    """Return headers dict with X-Internal-Token only if env var is set."""
    api_key = os.getenv("INTERNAL_API_KEY")
    if api_key:
        return {"X-Internal-Token": api_key}
    return {}


def _extract_user(event):
    """Extract user object from a MAX event or callback.

    Supports: MessageCreated, CallbackButton, and BotStarted events.
    Returns None if no user can be extracted.
    """
    if hasattr(event, "callback") and event.callback:
        return event.callback.user
    elif hasattr(event, "message") and event.message:
        return getattr(event.message, "sender", None)
    else:
        return getattr(event, "user", None)


def get_user_ids(event):
    """Extract (max_user_id, chat_id) from a MAX event or callback.

    Shared utility — replaces inline getattr(user, 'user_id') patterns across handlers.
    Supports: MessageCreated, CallbackButton, and BotStarted events.
    """
    user = _extract_user(event)
    max_user_id = getattr(user, "user_id", None) or getattr(user, "id", None)

    if hasattr(event, "chat_id"):
        chat_id = str(event.chat_id)
    elif hasattr(event, "message") and event.message:
        chat_id = str(event.message.recipient.chat_id)
    else:
        chat_id = str(max_user_id)

    return max_user_id, chat_id
