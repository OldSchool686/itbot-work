import logging
import asyncio
from dotenv import load_dotenv

load_dotenv()

import os


def _patch_maxapi_send_message():
    """Monkey-patch maxapi Button.model_dump to serialize enum fields as strings.

    Fixes: MaxApiError code=400 proto.payload "Can't deserialize body"
    Root cause: maxapi calls model_dump() which returns enum objects (ButtonType.CALLBACK)
    instead of strings ("callback"), and aiohttp fails to serialize them to JSON.
    """
    from maxapi.types.attachments.buttons.button import Button as _Button

    original_model_dump = _Button.model_dump

    def patched_button_model_dump(self_, **kwargs):
        result = original_model_dump(self_, **kwargs)
        for key, value in list(result.items()):
            if hasattr(value, 'value'):
                result[key] = value.value
        return result

    _Button.model_dump = patched_button_model_dump


# Apply patch before any maxapi imports that might cache references
_patch_maxapi_send_message()

from maxapi import Bot, Dispatcher

logger = logging.getLogger(__name__)


async def main():
    """Bot entry point."""
    from bot.fsm.middleware import init_storage, shutdown_storage

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    await init_storage(redis_url)

    try:
        # Initialize bot and dispatcher
        bot = Bot(token=os.getenv("MAX_BOT_TOKEN"))
        dp = Dispatcher()

        # Register handler routers (imported lazily to avoid circular deps)
        from bot.handlers import start_handler, menu_handler, help_handler, ticket_handler, bot_started

        bot_started.register_handlers(dp)
        start_handler.register_handlers(dp)
        menu_handler.register_handlers(dp)
        help_handler.register_handlers(dp)
        ticket_handler.register_handlers(dp)

        # Register bot commands for autocomplete hints in MAX mobile client
        try:
            from maxapi.types import BotCommand
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                await bot.set_my_commands(
                    BotCommand(name="start", description="Запустить бота"),
                    BotCommand(name="старт", description="Запустить бота (рус)"),
                    BotCommand(name="начать", description="Начать работу с ботом"),
                )
            logger.info("Bot commands registered: start, старт, начать")
        except Exception as e:
            logger.warning(f"Failed to register bot commands: {e}")

        # Remove existing webhook subscriptions before polling (MAX API blocks polling if webhooks exist)
        try:
            subs = await bot.get_subscriptions()
            for sub in getattr(subs, "subscriptions", []):
                await bot.unsubscribe_webhook(url=sub.url)
                logger.info(f"Removed webhook subscription: {sub.url}")
        except Exception as e:
            logger.warning(f"Could not unsubscribe webhooks: {e}")

        # Start polling (webhook mode handled via BaseMaxWebhook in separate module)
        logger.info("Starting bot in POLLING mode")
        await dp.start_polling(bot)
    finally:
        await shutdown_storage()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
