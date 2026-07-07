import logging

from maxapi.dispatcher import Dispatcher, Router
from maxapi.filters import Contact, F
from maxapi.filters.filter import BaseFilter
from maxapi.types import CallbackButton, Command, MessageCreated, RequestContactButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from bot.utils.consent_messages import consent_message, CONSENT_WITHDRAWN
from bot.utils import _extract_user, get_user_ids

logger = logging.getLogger(__name__)

router = Router()


class RussianStart(BaseFilter):
    """Passes only 'старт', '/старт', 'начать', '/начать' text messages."""

    async def __call__(self, event) -> bool:
        if not hasattr(event, "message") or not hasattr(event.message, "body"):
            return False
        body = getattr(event.message.body, "text", None)
        if not body:
            return False
        text = body.strip().lower()
        return text in ("старт", "/старт", "начать", "/начать")


@router.message_created(Command("start"))
async def handle_start(event: MessageCreated):
    """Handle /start command — check whitelist and route accordingly."""
    from bot.fsm.middleware import get_storage
    from bot.utils.whitelist_checker import get_whitelist_checker
    from bot.keyboards.main_keyboard import main_menu_keyboard, consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    checker = get_whitelist_checker()

    user = _extract_user(event)
    phone = getattr(user, "phone", None) if user else None

    # Try to identify user — first by phone (if MAX provides it), then by max_user_id
    result = None
    if phone:
        result = await checker.check(phone=phone)
    elif max_user_id:
        result = await checker.check(max_user_id=max_user_id)

    # If we found the user by either method, use their data
    if result and result.get("allowed"):
        user_data = result.get("user_data", {})
        stored_phone = user_data.get("phone", "") or (phone if phone else "")

        # Link max_user_id to allowed_users record (idempotent)
        if stored_phone:
            linked = await checker.link_user(stored_phone, max_user_id)
            if not linked:
                logger.warning(f"Failed to link max_user_id {max_user_id} to phone {stored_phone}")

        await storage.set_data(max_user_id, chat_id, {
            "phone": stored_phone,
            "full_name": user_data.get("full_name", ""),
            "department": user_data.get("department", ""),
        })

        if result.get("consent_given"):
            # User already has consent — go straight to menu
            await storage.set_state(max_user_id, chat_id, "main_menu")
            full_name = user_data.get("full_name", "Пользователь")
            await event.bot.send_message(
                chat_id=chat_id,
                text=f"Вы авторизованы как {full_name}. Добро пожаловать!",
                attachments=[main_menu_keyboard()],
            )
        else:
            # User exists but hasn't given consent yet
            await storage.set_state(max_user_id, chat_id, "consent_request")
            full_name = user_data.get("full_name", "")
            await event.bot.send_message(
                chat_id=chat_id,
                text=consent_message(full_name),
                attachments=[consent_keyboard()],
            )
        return

    # User not found in whitelist at all — welcome screen
    await storage.set_state(max_user_id, chat_id, "welcome")
    await event.bot.send_message(
        chat_id=chat_id,
        text=(
            "Добро пожаловать в чат-бот техподдержки ИТ-отдела!\n\n"
            "Я помогу вам:\n"
            "• Создать заявку ИТ-поддержки\n"
            "• Найти ответы в базе знаний (AI)\n"
            "• Найти шаблоны заявок для скачивания\n"
            "• Отследить статус ваших заявок\n\n"
            "Нажмите кнопку ниже, чтобы начать:"
        ),
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text="🚀 Старт", payload="start_button"))
            .as_markup()],
    )


@router.message_created(Contact())
async def handle_contact_received(event: MessageCreated):
    """Handle contact (phone number) received from user."""
    from bot.fsm.middleware import get_storage
    from bot.utils.whitelist_checker import get_whitelist_checker
    from bot.keyboards.main_keyboard import main_menu_keyboard, consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()

    # Extract phone from contact attachment per MAX API docs.
    # vcf_info format: "BEGIN:VCARD\r\nVERSION:3.0\r\n...TEL;TYPE=cell:7XXXXXXXXXX\r\nFN:...END:VCARD"
    import re

    phone = ""
    for att in (event.message.body.attachments or []):
        if hasattr(att, "type") and att.type == "contact":
            payload = getattr(att, "payload", None)
            if not payload:
                continue
            # Try max_info first (structured data from MAX API)
            max_info = getattr(payload, "max_info", None) or {}
            if isinstance(max_info, dict):
                phone = str(max_info.get("phone", ""))
            # Fallback: parse TEL line from vcf_info string
            if not phone and hasattr(payload, "vcf_info"):
                vcf_text = payload.vcf_info.replace("\r\n", "\n")
                from bot.utils import VCF_PHONE_PATTERN

                m = re.search(VCF_PHONE_PATTERN, vcf_text)
                if m:
                    phone = m.group(1)

    if not phone:
        await event.bot.send_message(
            chat_id=chat_id,
            text="Не удалось получить номер телефона. Пожалуйста, попробуйте снова.",
        )
        return

    try:
        checker = get_whitelist_checker()
        result = await checker.check(phone)

        if result and result.get("allowed"):
            current_state = await storage.get_state(max_user_id, chat_id)

            # Link max_user_id to allowed_users record (first time contact shared)
            linked = await checker.link_user(phone, max_user_id)
            if not linked:
                logger.warning(f"Failed to link max_user_id {max_user_id} to phone {phone}")

            user_data = result.get("user_data", {})
            await storage.set_data(max_user_id, chat_id, {
                "phone": phone,
                "full_name": user_data.get("full_name", ""),
                "department": user_data.get("department", ""),
            })

            # If consent was just given via callback (waiting_phone state), give consent then go to menu
            if current_state == "waiting_phone":
                from bot.utils.consent_checker import get_consent_checker
                consent_checker = get_consent_checker()
                await consent_checker.give_consent(phone)

                await storage.set_state(max_user_id, chat_id, "main_menu")
                full_name = user_data.get("full_name", "Пользователь")
                await event.bot.send_message(
                    chat_id=chat_id,
                    text=f"Вы авторизованы как {full_name}. Добро пожаловать!",
                    attachments=[main_menu_keyboard()],
                )
            elif result.get("consent_given"):
                # Consent already given by admin — go straight to menu
                await storage.set_state(max_user_id, chat_id, "main_menu")
                full_name = user_data.get("full_name", "Пользователь")
                await event.bot.send_message(
                    chat_id=chat_id,
                    text=f"Вы авторизованы как {full_name}. Добро пожаловать!",
                    attachments=[main_menu_keyboard()],
                )
            else:
                # User sent contact but hasn't given consent yet — show consent screen
                await storage.set_state(max_user_id, chat_id, "consent_request")
                full_name = user_data.get("full_name", "")
                await event.bot.send_message(
                    chat_id=chat_id,
                    text=consent_message(full_name),
                    attachments=[consent_keyboard()],
                )
        else:
            reason = (result or {}).get("reason", "not_found")
            await storage.set_state(max_user_id, chat_id, "access_denied")
            if reason == "deactivated":
                msg = "Ваш доступ временно отключён. Обратитесь в ИТ-отдел."
            else:
                msg = (
                    "Доступ ограничен. Обратитесь к администраторам.\n\n"
                    "Если вы считаете это ошибкой — отправьте /start ещё раз."
                )
            await event.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
        logger.error(f"Error processing contact for user {max_user_id}: {e}")
        await event.bot.send_message(
            chat_id=chat_id,
            text="Произошла ошибка при проверке доступа. Обратитесь в ИТ-отдел.",
        )


@router.message_callback(F.callback.payload == "start_button")
async def handle_start_button(event):
    """User clicked Start — show consent screen."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    full_name = session_data.get("full_name", "")

    await storage.set_state(max_user_id, chat_id, "consent_request")
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=consent_message(full_name),
        attachments=[consent_keyboard()],
    )


@router.message_callback(F.callback.payload == "consent_agree")
async def handle_consent_agree(event):
    """Handle consent agreement."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    phone = session_data.get("phone", "")
    full_name = session_data.get("full_name", "")
    
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")

    if not phone:
        # Phone not yet collected — request contact first, give consent later
        await storage.set_state(max_user_id, chat_id, "waiting_phone")
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "Спасибо за согласие!\n\n"
                "Для продолжения необходимо предоставить номер телефона.\n\n"
                "Нажмите кнопку ниже:"
            ),
            attachments=[InlineKeyboardBuilder()
                .row(RequestContactButton(text="📱 Отправить телефон"))
                .as_markup()],
        )
    else:
        # Phone already available — link user, give consent and go to menu
        from bot.utils.consent_checker import get_consent_checker
        from bot.utils.whitelist_checker import get_whitelist_checker

        checker = get_whitelist_checker()
        linked = await checker.link_user(phone, max_user_id)
        if not linked:
            logger.warning(f"Failed to link max_user_id {max_user_id} to phone {phone}")

        consent_checker = get_consent_checker()
        await consent_checker.give_consent(phone)

        await storage.set_state(max_user_id, chat_id, "main_menu")
        await event.bot.send_message(
            chat_id=chat_id,
            text=f"Спасибо! Вы авторизованы как {full_name}. Добро пожаловать!",
            attachments=[main_menu_keyboard()],
        )


@router.message_callback(F.callback.payload == "consent_decline")
async def handle_consent_decline(event):
    """Handle consent decline — deny access."""
    from bot.fsm.middleware import get_storage

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.set_state(max_user_id, chat_id, "consent_denied")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=CONSENT_WITHDRAWN,
        attachments=[InlineKeyboardBuilder()
            .row(CallbackButton(text="🔄 Изменить решение", payload="consent_retry"))
            .as_markup()],
    )


@router.message_callback(F.callback.payload == "consent_retry")
async def handle_consent_retry(event):
    """Show consent screen again — user changed their mind."""
    from bot.fsm.middleware import get_storage
    from bot.keyboards.main_keyboard import consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    session_data = await storage.get_data(max_user_id, chat_id)
    full_name = session_data.get("full_name", "")

    await storage.set_state(max_user_id, chat_id, "consent_request")

    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    await event.bot.send_message(
        chat_id=chat_id,
        text=consent_message(full_name),
        attachments=[consent_keyboard()],
    )


@router.message_created(RussianStart())
async def handle_russian_start(event: MessageCreated):
    """Handle 'старт', '/старт', 'начать', '/начать' as plain text → delegate to /start."""
    await handle_start(event)


def register_handlers(dp: Dispatcher):
    """Register all start handler routes with the dispatcher."""
    dp.include_routers(router)
