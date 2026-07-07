from maxapi.types import CallbackButton, RequestContactButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def main_menu_keyboard():
    """Build the main menu inline keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(CallbackButton(text="📝 Создать заявку", payload="create_ticket"))
        .row(CallbackButton(text="🔍 Поиск по базе знаний AI", payload="rag_search"))
        .row(CallbackButton(text="📋 Мои заявки", payload="my_tickets"))
        .row(
            CallbackButton(text="❓ Помощь", payload="help"),
            CallbackButton(text="⛔ Остановить", payload="stop"),
        )
        .as_markup()
    )


def consent_keyboard():
    """Build the consent (ПДн) agreement keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(
            CallbackButton(text="✅ Согласен", payload="consent_agree"),
            CallbackButton(text="❌ Не согласен", payload="consent_decline"),
        )
        .as_markup()
    )


def confirm_cancel_keyboard():
    """Generic confirm/cancel keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(
            CallbackButton(text="✅ Да", payload="confirm_yes"),
            CallbackButton(text="❌ Нет", payload="cancel"),
        )
        .as_markup()
    )


def continue_menu_keyboard():
    """Continue or back to menu keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(
            CallbackButton(text="🔄 Ещё вопрос", payload="rag_search"),
            CallbackButton(text="📋 Меню", payload="main_menu"),
        )
        .as_markup()
    )


def request_contact_keyboard():
    """Request contact button keyboard."""
    return (
        InlineKeyboardBuilder()
        .row(RequestContactButton(text="📱 Отправить телефон"))
        .as_markup()
    )
