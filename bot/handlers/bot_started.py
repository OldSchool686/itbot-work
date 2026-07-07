import logging

from maxapi.dispatcher import Router
from maxapi.types import BotStarted

from bot.utils.consent_messages import CONSENT_BODY, consent_welcome
from bot.utils import _extract_user, get_user_ids

logger = logging.getLogger(__name__)

router = Router()


@router.bot_started()
async def handle_bot_started(event: BotStarted):
    """Handle 'Start' button click — first launch of the bot.

    Saves user profile from MAX, distinguishes new vs returning users,
    then routes through whitelist + consent flow.
    """
    from bot.fsm.middleware import get_storage
    from bot.utils.whitelist_checker import get_whitelist_checker
    from bot.keyboards.main_keyboard import main_menu_keyboard, consent_keyboard

    max_user_id, chat_id = get_user_ids(event)

    # Extract MAX profile data
    user = _extract_user(event)
    first_name = (getattr(user, "first_name", None) or "") if user else ""
    last_name = (getattr(user, "last_name", None) or "") if user else ""
    phone = getattr(user, "phone", None) if user else None
    display_name = (first_name or "").strip() or "Пользователь"

    storage = get_storage()
    checker = get_whitelist_checker()

    # Save/update user profile in backend — returns {is_new: bool, ...} or error default
    save_result = await checker.save_user(
        max_user_id=max_user_id,
        first_name=first_name,
        last_name=last_name,
    )
    if "error" in save_result:
        logger.warning(f"Failed to save user profile for {max_user_id}: {save_result['error']}")
    is_new = save_result.get("is_new", False)

    if is_new:
        logger.info(f"New user registered via bot_started: {display_name} (ID: {max_user_id})")

    # Check whitelist — by phone or max_user_id
    result = None
    if phone:
        result = await checker.check(phone=phone)
    elif max_user_id:
        result = await checker.check(max_user_id=max_user_id)

    # If user is in whitelist and allowed
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
            "full_name": user_data.get("full_name", display_name),
            "department": user_data.get("department", ""),
        })

        if result.get("consent_given"):
            # Already has consent — go straight to menu
            await storage.set_state(max_user_id, chat_id, "main_menu")
            full_name = user_data.get("full_name", display_name)
            greeting = (
                f"🎉 Добро пожаловать, {full_name}!\n\n"
                if is_new
                else f"👋 С возвращением, {full_name}!\n\n"
            )
            await event.bot.send_message(
                chat_id=chat_id,
                text=(
                    greeting +
                    "Чем могу помочь?\n\n"
                    "• Создать заявку ИТ-поддержки\n"
                    "• Найти ответы в базе знаний (AI)\n"
                    "• Отследить статус ваших заявок\n"
                    "• Найти шаблон для скачивания"
                ),
                attachments=[main_menu_keyboard()],
            )
        else:
            # Needs consent — existing consent_agree/decline handlers will process the callback
            await storage.set_state(max_user_id, chat_id, "consent_request")
            full_name = user_data.get("full_name", display_name)
            greeting_prefix = (
                f"🎉 Добро пожаловать, {full_name}!"
                if is_new
                else f"{'Здравствуйте, ' + full_name if full_name else 'Здравствуйте!'}"
            )
            await event.bot.send_message(
                chat_id=chat_id,
                text=f"{greeting_prefix}\n\n{CONSENT_BODY}",
                attachments=[consent_keyboard()],
            )
        return

    # User not in whitelist at all — show welcome + consent, then phone request after agree
    if is_new:
        logger.info(f"User {display_name} (ID: {max_user_id}) not in whitelist — showing welcome flow")

    await storage.set_state(max_user_id, chat_id, "consent_request")
    await event.bot.send_message(
        chat_id=chat_id,
        text=consent_welcome(is_new),
        attachments=[consent_keyboard()],
    )


def register_handlers(dp):
    """Register all bot_started handler routes with the dispatcher."""
    dp.include_routers(router)
