import logging

from maxapi.dispatcher import Dispatcher, Router
from maxapi.filters import F
from maxapi.types import Command, MessageCreated

logger = logging.getLogger(__name__)

router = Router()

HELP_TEXT = """🤖 **ИТ-поддержка — бот-помощник**

Доступные команды:

/start — Начать работу с ботом
/help — Показать это сообщение
/stop — Остановить бота

Основные функции:

📝 Создать заявку — пошаговая форма для обращения в ИТ-поддержку (компьютер, принтер, ПО, сертификаты)
🔍 Поиск по базе знаний — задайте вопрос и получите ответ из документации
📋 Мои заявки — просмотр статуса ваших обращений
🔒 Вы можете закрыть свою заявку в «Мои заявки», если проблема разрешилась сама

📞 **Служба ИТ:** +7 (49667) 31126"""


@router.message_callback(F.callback.payload == "help")
async def handle_help(event):
    """Show help message from callback."""
    await event.bot.send_callback(callback_id=event.callback.callback_id, notification=" ")
    chat_id = (
        str(event.message.recipient.chat_id) if event.message else None
    )
    if chat_id:
        await event.bot.send_message(chat_id=chat_id, text=HELP_TEXT)


@router.message_created(Command("help"))
async def handle_help_command(event: MessageCreated):
    """Handle /help command."""
    chat_id = str(event.message.recipient.chat_id)
    await event.bot.send_message(chat_id=chat_id, text=HELP_TEXT)


@router.message_created(Command("stop"))
async def handle_stop_command(event: MessageCreated):
    """Handle /stop command."""
    from bot.fsm.middleware import get_storage

    from bot.utils import get_user_ids
    max_user_id, chat_id = get_user_ids(event)

    storage = get_storage()
    await storage.delete_state(max_user_id, chat_id)

    await event.bot.send_message(
        chat_id=chat_id,
        text="Бот остановлен. Для возобновления работы отправьте /start.",
    )


def register_handlers(dp: Dispatcher):
    """Register help handler routes."""
    dp.include_routers(router)
