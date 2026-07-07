import base64

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def department_keyboard(departments: list[dict]):
    """Build a keyboard for selecting a department.

    Args:
        departments: List of dicts with 'name' and optional 'type' keys.
                    e.g., [{"name": "Отдел ИКТ", "type": "department"}]
    """
    builder = InlineKeyboardBuilder()
    
    for dept in departments[:25]:
        encoded_name = base64.urlsafe_b64encode(dept["name"].encode()).decode()
        builder.row(CallbackButton(
            text=dept["name"],
            payload=f"dep_{encoded_name}"
        ))

    if not departments:
        builder.row(CallbackButton(text="⬅️ Назад", payload="back"))

    return builder.as_markup()


def extract_department_name(callback_data: str) -> str | None:
    """Extract department name from base64-encoded callback data like 'dep_0L3QvNC5...'."""
    prefix = "dep_"
    if callback_data.startswith(prefix):
        try:
            encoded = callback_data[len(prefix):]
            return base64.urlsafe_b64decode(encoded).decode()
        except Exception:
            return None
    return None
