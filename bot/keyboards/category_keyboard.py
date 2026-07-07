from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def category_keyboard():
    """Build the ticket category selection keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(CallbackButton(text="💻 Компьютер/Ноутбук", payload="ticket_category_computer"))
        .row(CallbackButton(text="🖨️ МФУ/Принтер/Сканер", payload="ticket_category_mfu"))
        .row(CallbackButton(text="⚙️ Программное обеспечение", payload="ticket_category_software"))
        .row(CallbackButton(text="🔐 Сертификат/Подпись", payload="ticket_category_certificate"))
        .row(CallbackButton(text="📎 Другое", payload="ticket_category_other"))
        .row(CallbackButton(text="⬅️ Назад", payload="main_menu"))
        .as_markup()
    )


CATEGORY_LABELS = {
    "computer": "💻 Компьютер/Ноутбук",
    "mfu": "🖨️ МФУ/Принтер/Сканер",
    "software": "⚙️ Программное обеспечение",
    "certificate": "🔐 Сертификат/Подпись",
    "other": "📎 Другое",
}


def extract_category(callback_data: str) -> str | None:
    """Extract category slug from callback data like 'ticket_category_computer'."""
    prefix = "ticket_category_"
    if callback_data.startswith(prefix):
        cat = callback_data[len(prefix):]
        return cat if cat in CATEGORY_LABELS else None
    return None


def back_to_menu_keyboard():
    """Back to main menu keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(CallbackButton(text="📋 Главное меню", payload="main_menu"))
        .as_markup()
    )
