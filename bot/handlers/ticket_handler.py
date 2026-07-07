"""Ticket creation FSM — full flow from category to Bitrix24 deal."""
import logging
import os
import re

import httpx
from maxapi.dispatcher import Dispatcher, Router
from maxapi.filters import F
from maxapi.types import CallbackButton, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from bot.utils import MAX_PHOTOS_PER_TICKET, VCF_PHONE_PATTERN, get_user_ids

from bot.fsm.states import (
    STATE_TICKET_CATEGORY,
    STATE_TICKET_NAME,
    STATE_TICKET_PHONE,
    STATE_TICKET_DEPARTMENT,
    STATE_TICKET_DESCRIPTION,
    STATE_TICKET_PHOTOS,
    STATE_TICKET_CONFIRM,
)
from bot.keyboards.category_keyboard import category_keyboard, CATEGORY_LABELS, extract_category
from bot.keyboards.department_keyboard import department_keyboard, extract_department_name
from bot.utils import _internal_headers
from bot.utils.photo_handler import extract_photo_urls, format_photo_preview

logger = logging.getLogger(__name__)

router = Router()


# --- Helpers ---


async def _get_queue_text(client, backend_url, ticket_id, headers):
    try:
        pos_resp = await client.get(
            f"{backend_url}/api/v1/bot/ticket-position",
            params={"ticket_id": ticket_id},
            headers=headers,
        )
        if pos_resp.status_code == 200:
            position = pos_resp.json().get("position_in_queue", 0)
            return f"\n\nОжидайте обработки — перед вами ~{position} заявок."
    except Exception as e:
        logger.debug("ticket-position endpoint failed", exc_info=e)

    return ""


def _confirmation_keyboard():
    """Build edit buttons + confirm/cancel for confirmation screen."""
    return (
        InlineKeyboardBuilder()
        .row(CallbackButton(text="✏️ Изменить ФИО", payload="ticket_edit_name"))
        .row(CallbackButton(text="✏️ Изменить телефон", payload="ticket_edit_phone"))
        .row(CallbackButton(text="✏️ Изменить отдел", payload="ticket_edit_department"))
        .row(CallbackButton(text="✏️ Изменить категорию", payload="ticket_edit_category"))
        .row(CallbackButton(text="✏️ Изменить описание", payload="ticket_edit_description"))
        .row(
            CallbackButton(text="✅ Отправить", payload="ticket_submit"),
            CallbackButton(text="❌ Отмена", payload="ticket_cancel"),
        )
        .as_markup()
    )


async def _build_confirmation_text(session_data):
    """Build the full confirmation summary text."""
    cat_label = CATEGORY_LABELS.get(session_data["category"], session_data["category"])
    lines = [
        "📋 Проверьте данные заявки:",
        f"ФИО: {session_data['full_name']}",
        f"Телефон: {session_data['phone']}",
        f"Отдел: {session_data['department']}",
        f"Категория: {cat_label}",
        f"Описание: {session_data.get('description', '—')[:200]}",
    ]
    photo_preview = format_photo_preview(session_data.get("photo_urls", []))
    if photo_preview:
        lines.append(photo_preview)
    return "\n".join(lines)


# --- Input Validation ---

PHONE_RE = re.compile(r"^(\+7|8|7)?\d{10}$")

def _validate_phone(phone: str) -> bool:
    """Check phone is 10-12 digits (with optional +7/8 prefix)."""
    digits = re.sub(r"\D", "", phone.strip())
    return PHONE_RE.match(phone.strip()) is not None and 10 <= len(digits) <= 12


NAME_RE = re.compile(r"^[а-яА-ЯёЁa-zA-Z\s\-\']+$")

def _validate_name(name: str) -> bool:
    """Check name is at least 2 chars, letters/spaces/hyphens only."""
    stripped = name.strip()
    return len(stripped) >= 2 and NAME_RE.match(stripped) is not None


async def _submit_ticket(event, chat_id, session_data):
    """Submit ticket: create local record → Bitrix24 deal → link them together."""
    backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
    headers = _internal_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            ticket_resp = await client.post(
                f"{backend_url}/api/v1/tickets/create",
                json={
                    "full_name": session_data["full_name"],
                    "phone": session_data["phone"],
                    "department": session_data["department"],
                    "category": session_data["category"],
                    "description": session_data.get("description", ""),
                    "photo_urls": session_data.get("photo_urls"),
                },
                headers=headers,
            )

            if ticket_resp.status_code not in (200, 201):
                logger.error(f"Failed to create local ticket: {ticket_resp.text}")
                await event.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Ошибка при сохранении заявки. Попробуйте позже или обратитесь в ИТ-отдел.",
                    attachments=[InlineKeyboardBuilder()
                        .row(CallbackButton(text="📋 Главное меню", payload="main_menu"))
                        .as_markup()],
                )
                return

            ticket_id = ticket_resp.json().get("ticket_id")

            deal_resp = await client.post(
                f"{backend_url}/api/v1/bitrix/deal",
                json={
                    "full_name": session_data["full_name"],
                    "phone": session_data["phone"],
                    "department": session_data["department"],
                    "category": session_data["category"],
                    "description": session_data.get("description", ""),
                    "ticket_id": ticket_id,
                },
                headers=headers,
            )

            if deal_resp.status_code in (200, 201):
                result = deal_resp.json()
                bitrix_deal_id = result.get("bitrix_deal_id")

                if bitrix_deal_id:
                    await client.post(
                        f"{backend_url}/api/v1/tickets/link-deal",
                        json={"ticket_id": ticket_id, "bitrix_deal_id": bitrix_deal_id},
                        headers=headers,
                    )

                queue_text = await _get_queue_text(client, backend_url, ticket_id, headers)

                await event.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Заявка #{bitrix_deal_id or ticket_id} успешно создана!\n\nСпасибо за обращение. Мы свяжемся с вами в ближайшее время.{queue_text}",
                    attachments=[InlineKeyboardBuilder()
                        .row(CallbackButton(text="📋 Главное меню", payload="main_menu"))
                        .as_markup()],
                )
            else:
                logger.error(f"Failed to create Bitrix24 deal: {deal_resp.text}")
                queue_text = await _get_queue_text(client, backend_url, ticket_id, headers)

                await event.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Заявка #{ticket_id} сохранена!\n\nСпасибо за обращение. Мы свяжемся с вами в ближайшее время.{queue_text}",
                    attachments=[InlineKeyboardBuilder()
                        .row(CallbackButton(text="📋 Главное меню", payload="main_menu"))
                        .as_markup()],
                )

    except httpx.RequestError:
        await event.bot.send_message(
            chat_id=chat_id,
            text="❌ Сервис временно недоступен. Попробуйте позже.",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="📋 Главное меню", payload="main_menu"))
                .as_markup()],
        )


# --- State 1: Category Selection ---

@router.message_callback(F.callback.payload.startswith("ticket_category_"))
async def handle_ticket_category(event):
    """Handle category selection callback."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    category = extract_category(event.callback.payload)

    if not category:
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        return

    session_data = await storage.get_data(max_user_id, chat_id)
    session_data["category"] = category
    await storage.set_data(max_user_id, chat_id, session_data)

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_NAME)
    await _ask_name(event, max_user_id, chat_id, storage)


# --- State 2: Name Confirmation ---

async def _ask_name(event, max_user_id, chat_id, storage):
    """Ask user to confirm or change their name."""
    session_data = await storage.get_data(max_user_id, chat_id)
    backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")

    prefill_name = session_data.get("full_name", "")
    if not prefill_name and session_data.get("phone"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{backend_url}/api/v1/bot/user-by-phone",
                    params={"phone": session_data["phone"]},
                    headers=_internal_headers(),
                )
                if resp.status_code == 200:
                    user_info = resp.json()
                    prefill_name = user_info.get("full_name", "")
                    if not prefill_name:
                        prefill_name = session_data.get("full_name", "")
        except httpx.RequestError:
            logger.warning("Failed to fetch user info for name prefill")

    if not prefill_name:
        prefill_name = "Неизвестно"

    await event.bot.send_message(
        chat_id=chat_id,
        text=f"Ваше ФИО:\n\n{prefill_name}\n\nПодтвердите или введите корректное ФИО:",
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text=f"✅ {prefill_name}", payload="ticket_name_confirm"))
            .row(CallbackButton(text="✏️ Изменить", payload="ticket_name_edit"))
            .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_category"))
            .as_markup()],
    )


@router.message_callback(F.callback.payload == "ticket_name_confirm")
async def handle_ticket_name_confirm(event):
    """Confirm pre-filled name."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)

    if not session_data.get("full_name"):
        backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{backend_url}/api/v1/bot/user-by-phone",
                    params={"phone": session_data.get("phone", "")},
                    headers=_internal_headers(),
                )
                if resp.status_code == 200:
                    session_data["full_name"] = resp.json().get("full_name", "Неизвестно")
        except httpx.RequestError:
            session_data["full_name"] = "Неизвестно"

    await storage.set_data(max_user_id, chat_id, session_data)
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_phone(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_name_edit")
async def handle_ticket_name_edit(event):
    """Switch to name input mode."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_NAME)
    await event.bot.send_message(chat_id=chat_id, text="Введите ваше ФИО:")


# --- State 3: Phone Confirmation ---

async def _advance_to_phone(event, max_user_id, chat_id, storage):
    """Advance to phone confirmation state."""
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_PHONE)
    session_data = await storage.get_data(max_user_id, chat_id)
    phone = session_data.get("phone", "")

    if phone:
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Телефон:\n\n{phone}\n\nПодтвердите или введите корректный номер:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text=f"✅ {phone}", payload="ticket_phone_confirm"))
                .row(CallbackButton(text="✏️ Изменить", payload="ticket_phone_edit"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_name"))
                .as_markup()],
        )
    else:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Введите ваш номер телефона:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_name"))
                .as_markup()],
        )


@router.message_callback(F.callback.payload == "ticket_phone_confirm")
async def handle_ticket_phone_confirm(event):
    """Confirm phone and advance to department."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_department(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_phone_edit")
async def handle_ticket_phone_edit(event):
    """Switch to phone input mode."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_PHONE)
    await event.bot.send_message(chat_id=chat_id, text="Введите ваш номер телефона:")


# --- State 4: Department Selection ---

async def _advance_to_department(event, max_user_id, chat_id, storage):
    """Advance to department selection state."""
    from bot.fsm.middleware import get_storage

    await storage.set_state(max_user_id, chat_id, STATE_TICKET_DEPARTMENT)
    session_data = await storage.get_data(max_user_id, chat_id)
    prefilled_dept = session_data.get("department", "")

    text = f"Выберите ваш отдел{(' (текущий: ' + prefilled_dept + ')') if prefilled_dept else ''}:"
    await _show_department_keyboard(event, chat_id, text)


def _department_keyboard_with_buttons(departments: list[dict], has_dept: bool):
    """Build department keyboard with continue, manual input and back buttons."""
    builder = InlineKeyboardBuilder()

    for dept in departments[:23]:
        import base64 as b64
        encoded_name = b64.urlsafe_b64encode(dept["name"].encode()).decode()
        btn_text = dept.get("display", dept["name"])
        builder.row(CallbackButton(
            text=btn_text,
            payload=f"dep_{encoded_name}"
        ))

    if has_dept:
        builder.row(CallbackButton(text="✅ Продолжить", payload="ticket_dept_continue"))
    builder.row(CallbackButton(text="✏️ Ввести вручную", payload="ticket_dept_manual"))
    builder.row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_phone"))

    return builder.as_markup()


async def _show_department_keyboard(event, chat_id, text: str):
    """Fetch departments from backend and show keyboard."""
    # Fallback departments if backend is unavailable (display: short name for button)
    FALLBACK_DEPTS = [
        {"name": "Управление экономики и инвестиций", "display": "Управление экономики и инвестиций"},
        {"name": "Управление по правовому обеспечению и безопасности муниципального образования", "display": "Управление по правовому обеспечению..."},
        {"name": "Управление комплексного развития муниципального образования", "display": "Управление комплексного развития МО"},
        {"name": "Служба информационно-коммуникационных технологий (ИКТ)", "display": "Служба ИКТ"},
        {"name": "Служба координации жилищно-коммунального хозяйства", "display": "Служба ЖКХ"},
        {"name": "Служба сельского хозяйства", "display": "Служба сельского хозяйства"},
        {"name": "Служба потребительского рынка", "display": "Служба потребительского рынка"},
        {"name": "Служба экономики, социального развития и инвестиций", "display": "Служба экономики и соц. развития"},
        {"name": "Служба гражданской обороны, чрезвычайных ситуаций и пожарной безопасности", "display": "Служба ГО, ЧС и пожарной безопасности"},
        {"name": "Служба военно-учетной работы", "display": "Служба военно-учётной работы"},
        {"name": "Служба координации энергетики и благоустройства", "display": "Служба энергетики и благоустройства"},
        {"name": "Отдел бухгалтерского учета", "display": "Отдел бухгалтерского учёта"},
        {"name": "Отдел по обращениям граждан и делопроизводству", "display": "Отдел обращений граждан и ДП"},
        {"name": "Отдел по развитию местного самоуправления", "display": "Отдел развития МСУ"},
        {"name": "Отдел по земельным отношениям", "display": "Отдел земельных отношений"},
        {"name": "Отдел по имуществу", "display": "Отдел по имуществу"},
        {"name": "Отдел архитектуры, строительства, дорожного хозяйства и транспорта", "display": "Отдел архитектуры, строительства и транспорта"},
        {"name": "Отдел по жилищным вопросам", "display": "Отдел по жилищным вопросам"},
        {"name": "Отдел культуры и делам молодежи", "display": "Отдел культуры и молодёжи"},
        {"name": "Отдел по физической культуре и спорту", "display": "Отдел физкультуры и спорта"},
        {"name": "Бюджетный отдел", "display": "Бюджетный отдел"},
        {"name": "Отдел бюджетного учета, отчетности и исполнения бюджета", "display": "Отдел бюджетного учёта и отчётности"},
        {"name": "Юридический отдел", "display": "Юридический отдел"},
    ]

    backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")
    departments = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{backend_url}/api/v1/department-suggest?limit=50")
            if resp.status_code == 200:
                departments = resp.json()
                logger.debug(f"Loaded {len(departments)} departments from backend")
    except httpx.RequestError as e:
        logger.warning(f"Failed to fetch departments from backend ({e}), using fallback list")

    if not departments:
        departments = FALLBACK_DEPTS
    else:
        # Backend returns {"name": ..., "type": ...} — add short display names
        departments = [_shorten_dept(d) for d in departments]

    has_dept = "текущий:" in text
    await event.bot.send_message(
        chat_id=chat_id,
        text=text,
        attachments=[_department_keyboard_with_buttons(departments, has_dept)],
    )


def _shorten_dept(dept: dict) -> dict:
    """Add a 'display' key with shortened name if missing."""
    if "display" in dept:
        return dept
    name = dept["name"]
    short_map = {
        "Управление по правовому обеспечению и безопасности муниципального образования": "Управление по правовому обеспечению...",
        "Управление комплексного развития муниципального образования": "Управление комплексного развития МО",
        "Служба информационно-коммуникационных технологий (ИКТ)": "Служба ИКТ",
        "Служба координации жилищно-коммунального хозяйства": "Служба ЖКХ",
        "Служба экономики, социального развития и инвестиций": "Служба экономики и соц. развития",
        "Служба гражданской обороны, чрезвычайных ситуаций и пожарной безопасности": "Служба ГО, ЧС и пожарной безопасности",
        "Служба военно-учетной работы": "Служба военно-учётной работы",
        "Служба координации энергетики и благоустройства": "Служба энергетики и благоустройства",
        "Отдел по обращениям граждан и делопроизводству": "Отдел обращений граждан и ДП",
        "Отдел по развитию местного самоуправления": "Отдел развития МСУ",
        "Отдел архитектуры, строительства, дорожного хозяйства и транспорта": "Отдел архитектуры, строительства и транспорта",
        "Отдел бюджетного учета, отчетности и исполнения бюджета": "Отдел бюджетного учёта и отчётности",
    }
    result = dict(dept)
    result["display"] = short_map.get(name, name[:50] + ("..." if len(name) > 50 else ""))
    return result


@router.message_callback(F.callback.payload.startswith("dep_") | (F.callback.payload == "ticket_dept_manual"))
async def handle_department_select(event):
    """Handle department selection from keyboard."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    
    if event.callback.payload == "ticket_dept_manual":
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        session_data = await storage.get_data(max_user_id, chat_id)
        prefilled_dept = session_data.get("department", "")
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Введите название отдела:{(' (текущий: ' + prefilled_dept + ')') if prefilled_dept else ''}",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_phone"))
                .as_markup()],
        )
        return

    dept_name = extract_department_name(event.callback.payload)

    if not dept_name:
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        return

    session_data = await storage.get_data(max_user_id, chat_id)
    session_data["department"] = dept_name
    await storage.set_data(max_user_id, chat_id, session_data)

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=f"Отдел выбран:\n\n{dept_name}\n\nПродолжить к следующему шагу?",
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text="✅ Продолжить", payload="ticket_dept_continue"))
            .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_phone"))
            .as_markup()],
    )


@router.message_callback(F.callback.payload == "ticket_dept_continue")
async def handle_department_continue(event):
    """Continue with existing department — skip to description."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_description(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_back_to_phone")
async def handle_back_to_phone(event):
    """Go back to phone confirmation step."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_phone(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_back_to_name")
async def handle_back_to_name(event):
    """Go back to name confirmation step."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_NAME)
    await _ask_name(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_back_to_description")
async def handle_back_to_description(event):
    """Go back to description step."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_description(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_back_to_category")
async def handle_back_to_category(event):
    """Go back to category selection step."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_CATEGORY)
    await event.bot.send_message(
        chat_id=chat_id, 
        text="Выберите категорию проблемы:", 
        attachments=[category_keyboard()]
    )


# --- State 5: Description Input ---

async def _advance_to_description(event, max_user_id, chat_id, storage):
    """Advance to description input state."""
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_DESCRIPTION)
    session_data = await storage.get_data(max_user_id, chat_id)
    current_desc = session_data.get("description", "")

    if current_desc:
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Описание:\n\n{current_desc[:200]}\n\nВведите новое описание или продолжите:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="✅ Продолжить", payload="ticket_description_continue"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_department"))
                .as_markup()],
        )
    else:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Опишите проблему (до 500 символов):\n\nЧем подробнее опишете — тем быстрее мы сможем помочь.",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_department"))
                .as_markup()],
        )


@router.message_callback(F.callback.payload == "ticket_description_continue")
async def handle_description_continue(event):
    """Continue from description to photos."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)

    if not session_data.get("description"):
        await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
        await event.bot.send_message(chat_id=chat_id, text="Сначала опишите проблему:")
        return

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_photos(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_back_to_department")
async def handle_back_to_department(event):
    """Go back to department selection step."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_department(event, max_user_id, chat_id, storage)


# --- State 6: Photos ---

async def _advance_to_photos(event, max_user_id, chat_id, storage):
    """Advance to photo upload state."""
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_PHOTOS)
    await event.bot.send_message(
        chat_id=chat_id,
        text="Прикрепите фотографии (до 3 шт.) или пропустите:\n\n📷 Отправьте фото для вложения к заявке\n⏭️ Или нажмите кнопку пропуска",
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text="✅ Продолжить", payload="ticket_photos_continue"))
            .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_description"))
            .as_markup()],
    )


@router.message_callback(F.callback.payload == "ticket_photos_continue")
async def handle_ticket_photos_continue(event):
    """Continue from photos to confirmation."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_confirm(event, max_user_id, chat_id, storage)


@router.message_callback(F.callback.payload == "ticket_photos_skip")
async def handle_ticket_photos_skip(event):
    """Skip photo upload."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await _advance_to_confirm(event, max_user_id, chat_id, storage)


# --- State 7: Confirmation ---

async def _advance_to_confirm(event, max_user_id, chat_id, storage):
    """Advance to confirmation state."""
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_CONFIRM)
    session_data = await storage.get_data(max_user_id, chat_id)
    text = await _build_confirmation_text(session_data)

    await event.bot.send_message(
        chat_id=chat_id,
        text=text + "\n\nВыберите действие:",
        attachments=[_confirmation_keyboard()],
    )


@router.message_callback(F.callback.payload == "ticket_submit")
async def handle_ticket_submit(event):
    """Submit the ticket."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)

    # Rate limit: 60s cooldown between ticket submissions per user
    if not await storage.check_cooldown(max_user_id, "ticket_create", 60):
        await event.bot.send_message(
            chat_id=chat_id,
            text="⏰ Слишком частые заявки. Пожалуйста, подождите.",
        )
        return

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_cooldown(max_user_id, "ticket_create", 60)
    await event.bot.send_message(chat_id=chat_id, text="⏳ Создаём заявку...")
    await _submit_ticket(event, chat_id, session_data)

    ticket_fields = {"category", "description", "photo_urls"}
    clean = {k: v for k, v in session_data.items() if k not in ticket_fields}
    await storage.set_data(max_user_id, chat_id, clean)
    await storage.set_state(max_user_id, chat_id, "main_menu")


@router.message_callback(F.callback.payload == "ticket_cancel")
async def handle_ticket_cancel(event):
    """Cancel ticket creation."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import main_menu_keyboard

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)

    keep_fields = {"phone", "full_name", "department"}
    clean = {k: v for k, v in session_data.items() if k in keep_fields}
    await storage.set_data(max_user_id, chat_id, clean)
    await storage.set_state(max_user_id, chat_id, "main_menu")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text="Заявка отменена.",
        attachments=[main_menu_keyboard()],
    )


# --- Edit callbacks (from confirmation screen) ---

@router.message_callback(F.callback.payload == "ticket_edit_category")
async def handle_ticket_edit_category(event):
    """Re-open category selection."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_CATEGORY)
    await event.bot.send_message(
        chat_id=chat_id, 
        text="Выберите категорию проблемы:", 
        attachments=[category_keyboard()]
    )


@router.message_callback(F.callback.payload == "ticket_edit_name")
async def handle_ticket_edit_name(event):
    """Re-open name input."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    current_name = session_data.get("full_name", "")
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_NAME)

    if current_name:
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Текущее ФИО:\n\n{current_name}\n\nВведите новое ФИО или подтвердите:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text=f"✅ {current_name}", payload="ticket_name_confirm"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_category"))
                .as_markup()],
        )
    else:
        await event.bot.send_message(
            chat_id=chat_id, 
            text="Введите ваше ФИО:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_category"))
                .as_markup()],
        )


@router.message_callback(F.callback.payload == "ticket_edit_phone")
async def handle_ticket_edit_phone(event):
    """Re-open phone input."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    current_phone = session_data.get("phone", "")
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_PHONE)

    if current_phone:
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Текущий телефон:\n\n{current_phone}\n\nВведите новый номер или подтвердите:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text=f"✅ {current_phone}", payload="ticket_phone_confirm"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_name"))
                .as_markup()],
        )
    else:
        await event.bot.send_message(
            chat_id=chat_id, 
            text="Введите ваш номер телефона:",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_name"))
                .as_markup()],
        )


@router.message_callback(F.callback.payload == "ticket_edit_department")
async def handle_ticket_edit_department(event):
    """Re-open department selection."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    current_dept = session_data.get("department", "")
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_DEPARTMENT)

    text = f"Текущий отдел: {current_dept}\n\nВыберите другой отдел:"
    await _show_department_keyboard(event, chat_id, text)


@router.message_callback(F.callback.payload == "ticket_edit_description")
async def handle_ticket_edit_description(event):
    """Re-open description input."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    current_desc = session_data.get("description", "")
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await storage.set_state(max_user_id, chat_id, STATE_TICKET_DESCRIPTION)

    text = f"Текущее описание:\n\n{current_desc[:200]}\n\nВведите новое описание:" if current_desc else "Опишите проблему (до 500 символов):"
    await event.bot.send_message(
        chat_id=chat_id,
        text=text,
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text="✅ Продолжить", payload="ticket_description_continue"))
            .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_department"))
            .as_markup()],
    )


# --- Text message handlers for FSM states ---

async def _state_filter(target_states):
    """Factory for a state-based filter."""
    from bot.fsm.middleware import get_storage
    
    async def check(event):
        max_user_id, chat_id = get_user_ids(event)
        storage = get_storage()
        state = await storage.get_state(max_user_id, chat_id)
        return state in target_states
    return check


TICKET_TEXT_STATES = {
    STATE_TICKET_NAME,
    STATE_TICKET_PHONE,
    STATE_TICKET_DEPARTMENT,
    STATE_TICKET_DESCRIPTION,
    STATE_TICKET_PHOTOS,
}


@router.message_created()
async def handle_ticket_text_message(event):
    """Catch-all for text messages in ticket FSM states."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)
    storage = get_storage()
    state = await storage.get_state(max_user_id, chat_id)

    # Extract text body first — needed for RAG too
    body = getattr(event.message, "body", None) if hasattr(event, "message") and event.message else None
    raw_text = (getattr(body, "text", None) or "").strip() if body else ""

    session_data = await storage.get_data(max_user_id, chat_id)

    # Route by state
    if state == STATE_TICKET_NAME:
        if not raw_text:
            return
        name = raw_text.strip()
        if not _validate_name(name):
            await event.bot.send_message(
                chat_id=chat_id,
                text="❌ Введите ФИО буквами (минимум 2 символа):",
            )
            return
        session_data["full_name"] = name[:500]
        await storage.set_data(max_user_id, chat_id, session_data)
        await _advance_to_phone(event, max_user_id, chat_id, storage)

    elif state == STATE_TICKET_PHONE:
        if not raw_text:
            return
        phone = raw_text.strip()
        if not _validate_phone(phone):
            await event.bot.send_message(
                chat_id=chat_id,
                text="❌ Введите корректный номер телефона (10-12 цифр, например 79123456789 или +79123456789):",
            )
            return
        session_data["phone"] = phone[:20]
        await storage.set_data(max_user_id, chat_id, session_data)
        await _advance_to_department(event, max_user_id, chat_id, storage)

    elif state == STATE_TICKET_DEPARTMENT:
        if not raw_text:
            return
        dept = raw_text.strip()
        if len(dept) < 3:
            await event.bot.send_message(
                chat_id=chat_id,
                text="❌ Введите название отдела (минимум 3 символа):",
            )
            return
        session_data["department"] = dept[:200]
        await storage.set_data(max_user_id, chat_id, session_data)
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Отдел сохранён:\n\n{dept[:200]}\n\nПродолжить к следующему шагу?",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="✅ Продолжить", payload="ticket_dept_continue"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_phone"))
                .as_markup()],
        )

    elif state == STATE_TICKET_DESCRIPTION:
        if not raw_text:
            return
        session_data["description"] = raw_text[:500]
        await storage.set_data(max_user_id, chat_id, session_data)
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Описание сохранено:\n\n{raw_text[:200]}\n\nПродолжить к следующему шагу?",
            attachments=[InlineKeyboardBuilder()
                .row(CallbackButton(text="✅ Продолжить", payload="ticket_description_continue"))
                .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_department"))
                .as_markup()],
        )

    elif state == STATE_TICKET_PHOTOS:
        urls = extract_photo_urls(event)
        existing = session_data.get("photo_urls", [])
        combined = existing + [u for u in urls if u not in existing]
        session_data["photo_urls"] = combined[:3]
        await storage.set_data(max_user_id, chat_id, session_data)

        remaining = MAX_PHOTOS_PER_TICKET - len(session_data["photo_urls"])
        if remaining > 0:
            await event.bot.send_message(
                chat_id=chat_id,
                text=f"Фото принято ({len(session_data['photo_urls'])}/3). Отправьте ещё или пропустите:",
                attachments=[InlineKeyboardBuilder()
                    .row(CallbackButton(text="✅ Продолжить", payload="ticket_photos_continue"))
                    .row(CallbackButton(text="⬅️ Назад", payload="ticket_back_to_description"))
                    .as_markup()],
            )
        else:
            await _advance_to_confirm(event, max_user_id, chat_id, storage)

    elif state == "rag_question":
        await _handle_rag_question(event, max_user_id, chat_id, session_data, raw_text)


async def _handle_rag_question(event, max_user_id, chat_id, session_data, question_text: str):
    """Handle RAG knowledge base search query — orchestrator."""
    import logging
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import continue_menu_keyboard

    log = logging.getLogger(__name__)

    # Rate limit: 30s cooldown between RAG queries per user
    storage = get_storage()
    if not await storage.check_cooldown(max_user_id, "rag", 15):
        await event.bot.send_message(
            chat_id=chat_id,
            text="Пожалуйста, подождите перед следующим вопросом. Я отдышусь ))",
            attachments=[continue_menu_keyboard()],
        )
        return

    # Validate input length (1-2000 characters)
    if not question_text or len(question_text) > 2000:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Введите вопрос (1–2000 символов).",
            attachments=[continue_menu_keyboard()],
        )
        return

    log.info(f"RAG query uid={max_user_id} cid={chat_id}: {question_text[:100]}")

    # Load conversation history (last 5 exchanges max)
    rag_history = session_data.get("rag_history", [])[-10:] if len(session_data.get("rag_history", [])) > 10 else session_data.get("rag_history", [])

    try:
        backend_url = os.getenv("BACKEND_URL", "http://it_bot_backend:8000")

        # Single client for all requests (query + template links)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await _rag_query(client, backend_url, question_text, session_data.get("user_id"), rag_history)

            if resp.status_code != 200:
                raise _RagBackendError(resp.status_code, resp.text[:500])

            data = resp.json()
            answer = data.get("answer", "Не удалось получить ответ.")
            sources = data.get("sources", [])
            cached = data.get("cached", False)

            # Save conversation history
            await _save_rag_history(max_user_id, chat_id, session_data, question_text, answer)

            # Format answer with sources & templates
            answer += _format_sources(sources, cached, answer)
            template_buttons, fallback_lines = await _generate_template_links(client, backend_url, data.get("templates", []))
            answer = _append_template_info(answer, template_buttons, fallback_lines)

        # Build keyboard and send response
        kb = InlineKeyboardBuilder()
        for btn in template_buttons:
            kb.row(btn)
        kb.row(
            CallbackButton(text="🔄 Ещё вопрос", payload="rag_search"),
            CallbackButton(text="📋 Меню", payload="main_menu"),
        )

        await event.bot.send_message(
            chat_id=chat_id,
            text=answer,
            attachments=[kb.as_markup()],
        )

        # Set cooldown after successful response
        await storage.set_cooldown(max_user_id, "rag", 15)

    except _RagBackendError as e:
        log.error(f"RAG backend error {e.status_code}: {e.detail}")
        await storage.set_cooldown(max_user_id, "rag", 15)
        await event.bot.send_message(
            chat_id=chat_id,
            text="Такой инструкции или шаблона нет в базе...\n\n Сервис поиска временно недоступен. Попробуйте позже.\n\nЕсли вопрос срочный — создайте заявку через меню.",
            attachments=[continue_menu_keyboard()],
        )

    except httpx.TimeoutException:
        log.warning(f"RAG timeout uid={max_user_id}")
        await storage.set_cooldown(max_user_id, "rag", 15)
        await event.bot.send_message(
            chat_id=chat_id,
            text="Превышено время ожидания ответа. Попробуйте переформулировать вопрос или создайте заявку.",
            attachments=[continue_menu_keyboard()],
        )

    except httpx.RequestError as e:
        log.error(f"RAG request error: {e}")
        await storage.set_cooldown(max_user_id, "rag", 15)
        await event.bot.send_message(
            chat_id=chat_id,
            text="Ошибка соединения с сервером. Попробуйте позже.",
            attachments=[continue_menu_keyboard()],
        )

    except Exception as e:
        log.exception(f"RAG handler error uid={max_user_id}: {e}")
        await event.bot.send_message(
            chat_id=chat_id,
            text="Произошла ошибка. Попробуйте позже или создайте заявку через меню.",
            attachments=[continue_menu_keyboard()],
        )


class _RagBackendError(Exception):
    """Raised when RAG backend returns non-200 status."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


async def _rag_query(client: httpx.AsyncClient, backend_url: str, question_text: str, user_id, rag_history: list) -> httpx.Response:
    """Query RAG backend and return response."""
    return await client.post(
        f"{backend_url}/api/v1/rag/query",
        json={
            "query_text": question_text,
            "user_id": user_id,
            "conversation_history": rag_history,
        },
        headers=_internal_headers(),
    )


async def _save_rag_history(max_user_id: str, chat_id: str, session_data: dict, question: str, answer: str) -> None:
    """Append Q&A to history and save to Redis (max 10 entries)."""
    from bot.fsm.middleware import get_storage

    rag_history = session_data.get("rag_history", [])
    rag_history.append({"question": question, "answer": answer})
    if len(rag_history) > 10:
        rag_history = rag_history[-10:]
    session_data["rag_history"] = rag_history
    storage = get_storage()
    await storage.set_data(max_user_id, chat_id, session_data)


def _format_sources(sources: list, cached: bool, answer_text: str = "") -> str:
    """Format source citations and cache indicator."""
    result = ""

    if sources:
        lines = [f"• {src.get('filename', 'unknown')} (фрагмент #{src.get('chunk_index', 0)})" for src in sources[:3]]
        result += "\n\n📚 Источники:\n" + "\n".join(lines)

    if cached:
        result += "\n\n(ответ из кэша)"

    if not sources and "не нашёл" in answer_text.lower():
        result += "\n\n💡 Не нашли ответ? Создайте заявку через меню."

    return result


async def _generate_template_links(client: httpx.AsyncClient, backend_url: str, templates: list):
    """Generate download links for template documents. Returns (buttons, fallback_lines)."""
    import logging

    log = logging.getLogger(__name__)
    buttons: list[LinkButton] = []
    fallbacks: list[str] = []

    for tmpl in templates[:5]:
        tpl_id = tmpl.get("id")
        tpl_name = tmpl.get("filename", "template")
        try:
            link_resp = await client.post(
                f"{backend_url}/api/v1/templates/{tpl_id}/generate-link",
            )

            if link_resp.status_code == 200:
                download_url = link_resp.json().get("download_url", "")
                log.info(f"Template {tpl_id} ({tpl_name}): download_url={download_url}")
                if download_url and (download_url.startswith("http://") or download_url.startswith("https://")):
                    buttons.append(LinkButton(text=tpl_name, url=download_url))
                else:
                    log.warning(f"Template {tpl_id}: invalid URL format: {download_url}")
                    fallbacks.append(tpl_name)
            else:
                fallbacks.append(tpl_name)
        except Exception as tmpl_err:
            log.warning(f"Failed to generate template link for {tpl_id}: {tmpl_err}")
            fallbacks.append(tpl_name)

    return buttons, fallbacks


def _append_template_info(answer: str, buttons: list[LinkButton], fallbacks: list[str]) -> str:
    """Append template download info to answer text."""
    result = answer
    for name in fallbacks:
        result += f"\n\n📎 {name} (ссылка недоступна)"
    if buttons:
        result += "\n\nНажмите на кнопку для скачивания:"
    return result


def register_handlers(dp: Dispatcher):
    """Register all ticket handler routes with the dispatcher."""
    dp.include_routers(router)
