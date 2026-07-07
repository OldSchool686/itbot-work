import logging

from maxapi.dispatcher import Dispatcher, Router
from maxapi.filters import F

from bot.utils import TICKETS_LIST_LIMIT, get_user_ids

from bot.utils import _internal_headers

logger = logging.getLogger(__name__)

router = Router()


# --- Ticket list rendering helper ---

_STATUS_LABELS = {
    "new": "🔵 Новая",
    "in_progress": "🟡 В работе",
    "done": "✅ Выполнена",
    "closed": "⚫ Закрыта",
}

_CATEGORY_LABELS = {
    "computer": "💻 Компьютер/Ноутбук",
    "mfu": "🖨️ МФУ/Принтер/Сканер",
    "software": "⚙️ Программное обеспечение",
    "certificate": "🔐 Сертификат/Подпись",
    "other": "📎 Другое",
}


async def _render_tickets_list(phone: str) -> tuple[str, object | None]:
    """Fetch and render the user's ticket list.

    Returns (text, inline_keyboard_markup_or_None).
    """
    import httpx, os
    from maxapi.types import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{backend_url}/api/v1/bot/user-tickets",
                params={"phone": phone},
                headers=_internal_headers(),
            )
    except httpx.RequestError:
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text="📋 Меню", payload="main_menu"))
        return "Сервис временно недоступен. Попробуйте позже.", kb.as_markup()

    if resp.status_code != 200:
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text="📋 Меню", payload="main_menu"))
        return "Возникла ошибка при получении заявок. Попробуйте позже.", kb.as_markup()

    tickets = resp.json().get("tickets", [])
    if not tickets:
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text="📋 Меню", payload="main_menu"))
        return "У вас пока нет заявок.", kb.as_markup()

    lines = []
    close_buttons = []
    for t in tickets[:TICKETS_LIST_LIMIT]:
        date_str = ""
        if t.get("created_at"):
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(t["created_at"])
                date_str = f" {dt.strftime('%d.%m %H:%M')}"
            except (ValueError, TypeError):
                pass

        cat_raw = t.get("category", "")
        cat = _CATEGORY_LABELS.get(cat_raw, cat_raw)
        desc = (t.get("description", "") or "")[:80]
        st = _STATUS_LABELS.get(t.get("status", ""), f"⚪ {t.get('status', '')}")

        line = f"📌 #{t['id']}{date_str}\n{cat} — {st}"
        if desc:
            line += f"\n  {desc}"
        lines.append(line)

        if t.get("status") != "closed":
            close_buttons.append(t["id"])

    text = ("Ваши последние заявки:\n\n"
             f"{'\n\n'.join(lines)}\n\n"
             "🔒 Вы можете закрыть свою заявку, если проблема разрешилась сама")

    kb = InlineKeyboardBuilder()
    for tid in close_buttons:
        kb.row(CallbackButton(text=f"🔒 Закрыть заявку #{tid}", payload=f"close_ticket:{tid}"))
    kb.row(CallbackButton(text="📋 Меню", payload="main_menu"))
    return text, kb.as_markup()


async def _send_tickets_list(bot, chat_id: str, phone: str):
    """Send the rendered ticket list message."""
    text, kb = await _render_tickets_list(phone)
    attachments = [kb] if kb is not None else None
    await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)


@router.message_callback(F.callback.payload == "main_menu")
async def handle_main_menu(event):
    """Show the main menu."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import main_menu_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.set_state(max_user_id, chat_id, "main_menu")

    session_data = await storage.get_data(max_user_id, chat_id)
    full_name = session_data.get("full_name", "")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=f"Добро пожаловать, {full_name}! Чем могу помочь?",
        attachments=[main_menu_keyboard()],
    )


@router.message_callback(F.callback.payload == "create_ticket")
async def handle_create_ticket(event):
    """Start ticket creation flow — show category selection."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.category_keyboard import category_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.set_state(max_user_id, chat_id, "ticket_category")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text="Выберите категорию проблемы:",
        attachments=[category_keyboard()],
    )


@router.message_callback(F.callback.payload == "rag_search")
async def handle_rag_search(event):
    """Start RAG knowledge base search."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    current_state = await storage.get_state(max_user_id, chat_id)

    # Clear conversation history when starting fresh from menu.
    # Preserve it when coming back via "Ещё вопрос" (state is already rag_question).
    if current_state != "rag_question":
        session_data = await storage.get_data(max_user_id, chat_id)
        session_data.pop("rag_history", None)
        await storage.set_data(max_user_id, chat_id, session_data)

    await storage.set_state(max_user_id, chat_id, "rag_question")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text="Задайте вопрос по базе знаний:\n\nНапример: «Как сменить пароль мосрег?» или «как получить эцп?»\n\nЕсли вам нужен шаблон заявки для доступа, добавления, изменения и пр. в информационных системах, начните свой запрос со слова «скачать». Например: «скачать шаблон екп»",
    )


@router.message_callback(F.callback.payload == "my_tickets")
async def handle_my_tickets(event):
    """Show user's recent tickets."""
    max_user_id, chat_id = get_user_ids(event)

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")

    from bot.fsm.middleware import get_storage
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    phone = session_data.get("phone", "")

    if not phone:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Не удалось определить ваш номер телефона. Отправьте /start заново.",
        )
        return

    await _send_tickets_list(event.bot, chat_id, phone)


@router.message_callback(F.callback.payload.startswith("close_ticket:"))
async def handle_close_ticket(event):
    """Show confirmation dialog for closing a ticket."""
    from bot.fsm.states import STATE_CLOSING_TICKET

    try:
        ticket_id = int(event.callback.payload.split(":", 1)[1])
    except (ValueError, IndexError):
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        logger.warning(f"Invalid close_ticket payload: {event.callback.payload}")
        return

    max_user_id, chat_id = get_user_ids(event)

    from bot.fsm.middleware import get_storage
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    session_data["closing_ticket_id"] = ticket_id
    await storage.set_data(max_user_id, chat_id, session_data)
    await storage.set_state(max_user_id, chat_id, STATE_CLOSING_TICKET)

    logger.info(f"User {max_user_id} requested close for ticket #{ticket_id}")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")

    from maxapi.types import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    confirm_kb = (InlineKeyboardBuilder()
        .row(
            CallbackButton(text="✅ Да, закрыть", payload="confirm_close_yes"),
            CallbackButton(text="❌ Отмена", payload="confirm_close_no"),
        )
        .as_markup())

    await event.bot.send_message(
        chat_id=chat_id,
        text=f"Закрыть заявку #{ticket_id}?\n\nЕсли проблема решена — нажмите «Да».",
        attachments=[confirm_kb],
    )


@router.message_callback(F.callback.payload == "confirm_close_yes")
async def handle_confirm_close(event):
    """Confirm and close the ticket via backend API."""
    import httpx, os

    max_user_id, chat_id = get_user_ids(event)

    from bot.fsm.middleware import get_storage
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    ticket_id = session_data.pop("closing_ticket_id", None)
    phone = session_data.get("phone", "")
    await storage.set_data(max_user_id, chat_id, session_data)

    if not ticket_id or not phone:
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        logger.warning(f"Close cancelled — missing data (ticket_id={ticket_id}, phone present={bool(phone)}) uid={max_user_id}")
        await _send_tickets_list(event.bot, chat_id, phone)
        return

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    logger.info(f"Closing ticket #{ticket_id} for uid={max_user_id}, phone={phone}")

    try:
        backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{backend_url}/api/v1/bot/close-ticket",
                json={"ticket_id": ticket_id, "phone": phone},
                headers=_internal_headers(),
            )

        if resp.status_code == 200:
            logger.info(f"Ticket #{ticket_id} closed successfully")
            await storage.set_state(max_user_id, chat_id, "main_menu")
            await event.bot.send_message(
                chat_id=chat_id,
                text=f"✅ Заявка #{ticket_id} закрыта. Спасибо!",
            )
            await _send_tickets_list(event.bot, chat_id, phone)
        else:
            try:
                msg = resp.json().get("message", "Ошибка при закрытии заявки")
            except Exception:
                msg = f"Ошибка при закрытии заявки (код {resp.status_code})"
            logger.warning(f"Close ticket #{ticket_id} failed ({resp.status_code}): {msg}")
            await event.bot.send_message(chat_id=chat_id, text=msg)

    except httpx.RequestError as e:
        logger.error(f"Backend unreachable when closing ticket #{ticket_id}: {e}")
        await event.bot.send_message(
            chat_id=chat_id,
            text="Сервис временно недоступен. Попробуйте позже.",
        )


@router.message_callback(F.callback.payload == "confirm_close_no")
async def handle_cancel_close(event):
    """Cancel closing a ticket."""
    max_user_id, chat_id = get_user_ids(event)

    from bot.fsm.middleware import get_storage
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    phone = session_data.get("phone", "")
    session_data.pop("closing_ticket_id", None)
    await storage.set_data(max_user_id, chat_id, session_data)
    await storage.set_state(max_user_id, chat_id, "main_menu")

    logger.info(f"User {max_user_id} cancelled ticket close")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _send_tickets_list(event.bot, chat_id, phone)


@router.message_callback(F.callback.payload == "help")
async def handle_help(event):
    """Show help message."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.set_state(max_user_id, chat_id, "main_menu")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=(
            "🤖 **ИТ-бот поддержки**\n\n"
            "📝 /start — начать работу с ботом\n"
            "❓ /help — показать эту справку\n"
            "⏹️  /stop — остановить бота\n\n"
            "**Меню:**\n"
            "• Создать заявку — пошаговое создание заявки ИТ-поддержки\n"
            "• Поиск по базе знаний — AI поиск по документации\n"
            "• Мои заявки — список ваших последних заявок\n"
            "🔒 Вы можете закрыть свою заявку в «Мои заявки», если проблема разрешилась сама"
        ),
    )


@router.message_callback(F.callback.payload == "stop")
async def handle_stop(event):
    """Stop bot interaction."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.delete_state(max_user_id, chat_id)

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text="Бот остановлен. Для возобновления работы отправьте /start.",
    )


def register_handlers(dp: Dispatcher):
    """Register all menu handler routes."""
    dp.include_routers(router)
