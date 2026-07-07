import logging
import asyncio
import os
from functools import wraps
from dotenv import load_dotenv

load_dotenv()


def _patch_maxapi_send_message():
    """Patch maxapi's send_message to use proper JSON serialization.

    Bypasses pydantic v2 model_dump bug by serializing attachments manually
    at the HTTP layer, before aiohttp tries to serialize them.
    """
    import json as _json
    from enum import Enum

    from maxapi.methods.send_message import SendMessage
    from pydantic import BaseModel

    original_fetch = SendMessage.fetch

    def _serialize(obj):
        """Recursively convert object to JSON-serializable dict, excluding fields marked exclude."""
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, BaseModel):
            result = {}
            for fname in type(obj).model_fields:
                fi = type(obj).model_fields[fname]
                if getattr(fi, "exclude", False):
                    continue
                val = getattr(obj, fname)
                result[fname] = _serialize(val)
            return result
        if isinstance(obj, list):
            return [_serialize(item) for item in obj]
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        return obj

    @wraps(original_fetch)
    async def patched_fetch(self):
        # Build JSON manually instead of using pydantic model_dump
        from maxapi.types.attachments.attachment import Attachment
        from maxapi.types.attachments.buttons.attachment_button import AttachmentButton
        from maxapi.types.attachments.upload import AttachmentUpload
        from maxapi.types.input_media import InputMedia, InputMediaBuffer

        bot = self._ensure_bot()
        params = bot.params.copy()

        json_data: dict = {"attachments": []}

        if self.chat_id:
            params["chat_id"] = self.chat_id
        elif self.user_id:
            params["user_id"] = self.user_id

        if self.disable_link_preview is not None:
            params["disable_link_preview"] = str(self.disable_link_preview).lower()

        if self.text is not None:
            json_data["text"] = self.text

        has_input_media = False

        if self.attachments:
            for att in self.attachments:
                if isinstance(att, AttachmentButton) and not any(
                    att.payload.buttons
                ):
                    continue

                if isinstance(att, (InputMedia, InputMediaBuffer)):
                    has_input_media = True
                    json_data["attachments"].append(_serialize(att))
                elif isinstance(att, Attachment) and isinstance(
                    att.payload, AttachmentUpload
                ):
                    json_data["attachments"].append(_serialize(att.payload))
                else:
                    json_data["attachments"].append(_serialize(att))

        if self.link is not None:
            json_data["link"] = _serialize(self.link)

        if self.notify is not None:
            json_data["notify"] = self.notify

        if self.format is not None:
            json_data["format"] = self.format.value if isinstance(self.format, Enum) else str(self.format)

        # Log the JSON for debugging
        logger.debug(f"SendMessage payload: {_json.dumps(json_data, ensure_ascii=False)}")

        import time as _time
        from maxapi.enums.api_path import ApiPath
        from maxapi.enums.http_method import HTTPMethod
        from maxapi.exceptions.max import MaxApiError
        from maxapi.methods.types.sended_message import SendedMessage
        from typing import cast

        attempts = bot.after_upload_attempts
        retry_delay = bot.after_upload_retry_delay
        give_up_timeout = bot.after_upload_give_up_timeout

        response = None
        start_time = _time.monotonic()
        for attempt in range(attempts):
            try:
                response = await super(SendMessage, self).request(
                    method=HTTPMethod.POST,
                    path=ApiPath.MESSAGES,
                    model=SendedMessage,
                    params=params,
                    json=json_data,  # Already serialized — aiohttp won't touch it
                )
            except MaxApiError as e:
                if (
                    isinstance(e.raw, dict)
                    and e.raw.get("code") == "attachment.not.ready"
                ):
                    elapsed = _time.monotonic() - start_time
                    if (
                        give_up_timeout is not None
                        and elapsed + retry_delay > give_up_timeout
                    ):
                        raise RuntimeError(
                            f"Превышено максимальное время ожидания готовности медиа "
                            f"({give_up_timeout}с), прошло {elapsed:.1f}с"
                        ) from e
                    logger.info(
                        f"Ошибка при отправке загруженного медиа, попытка {attempt + 1}, жду {retry_delay} секунды"
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    raise e

            break

        if response is None:
            raise RuntimeError("Не удалось отправить сообщение")

        return cast(SendedMessage | None, response)

    SendMessage.fetch = patched_fetch


_patch_maxapi_send_message()

from maxapi import Bot, Dispatcher
from maxapi.webhook.aiohttp import AiohttpMaxWebhook

logger = logging.getLogger(__name__)


MINI_APP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ИТ-поддержка</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f4f8;color:#1a202c;line-height:1.6;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.card{background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:420px;width:100%;padding:32px;text-align:center}
.icon{font-size:56px;margin-bottom:12px}
h1{font-size:22px;color:#0f766e;margin-bottom:8px}
p.sub{color:#64748b;font-size:14px;margin-bottom:20px}
ul{list-style:none;text-align:left;padding:0 8px;margin-bottom:24px}
li{padding:8px 0;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;gap:10px;font-size:14px;color:#334155}
li:last-child{border:none}
.btn{display:inline-block;padding:20px 32px;background:#0f766e;color:#fff;border:none;border-radius:14px;font-size:18px;font-weight:600;cursor:pointer;text-decoration:none;width:100%;transition:all .2s;box-shadow:0 4px 12px rgba(15,118,110,.3)}
.btn:hover{background:#0d9488;transform:translateY(-1px);box-shadow:0 6px 16px rgba(15,118,110,.4)}
.info-box{display:none;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:16px;margin-top:16px;text-align:left}
.info-box p{font-size:14px;color:#9a3412;line-height:1.7;margin:0 0 8px}
.cmds{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.cmd{background:#ffedd5;padding:4px 10px;border-radius:6px;font-weight:600;color:#9a3412;font-size:14px}
.close-hint{font-size:12px;color:#b45309;margin-top:8px;text-align:center}
.footer{margin-top:16px;font-size:12px;color:#94a3b8}
</style>
</head>
<body>
<div class="card">
  <div class="icon">&#x1F6E0;&#xFE0F;</div>
  <h1>Чат-бот службы ИТ</h1>
  <ul>
    <li>&#x1F4DD; Создать заявку на техподдержку</li>
    <li>&#x1F50D; Поиск по базе знаний (AI)</li>
    <li>&#x1F4CB; Отследить статус заявок</li>
    <li>&#x1F4CB; Скачать шаблон заявки</li>
  </ul>
  <button class="btn" onclick="startBot()">&#x1F680; Начать</button>
  <div id="infoBox" class="info-box">
    <p>&#x2753; Напишите в чате любую команду:</p>
    <div class="cmds">
      <span class="cmd">/start</span>
      <span class="cmd">/старт</span>
      <span class="cmd">/начать</span>
    </div>
    <div class="close-hint">&#x2190; Закройте это окно через интерфейс MAX (крестик или свайп)</div>
  </div>
  <div class="footer">&#x1F578;&#xFE0F; Служба ИКТ &middot; +7(49667)31-126</div>
</div>
<script>
function startBot(){
document.getElementById('infoBox').style.display='block';
}
</script>
</body>
</html>"""


async def main():
    """Bot webhook entry point."""
    from bot.fsm.middleware import init_storage, shutdown_storage

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    await init_storage(redis_url)

    try:
        # Initialize bot and dispatcher
        bot = Bot(token=os.getenv("MAX_BOT_TOKEN"))
        dp = Dispatcher()

        # Register handler routers (bot_started MUST be first to catch BotStarted events)
        from bot.handlers import (
            start_handler, menu_handler, help_handler, ticket_handler,
            bot_started,
        )

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

        # Subscribe webhook URL with MAX API (auth via MAX_BOT_TOKEN)
        webhook_url = os.getenv("MAX_WEBHOOK_URL", "https://bot.spadm.ru/webhook/max")

        try:
            await bot.subscribe_webhook(url=webhook_url)
            logger.info(f"Webhook subscribed: {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to subscribe webhook: {e}")
            raise

        # Build aiohttp app with both POST (webhook) and GET (mini-app page)
        from aiohttp import web

        aio_webhook = AiohttpMaxWebhook(dp=dp, bot=bot)
        app = aio_webhook.create_app(path="/")

        async def miniapp_handler(request):
            """GET handler — returns HTML mini-app landing page for MAX 'Start' button."""
            return web.Response(text=MINI_APP_HTML, content_type="text/html")

        app.router.add_get("/", miniapp_handler)

        # Internal endpoint: admin sends messages (with optional file attachments) to MAX users
        _MAX_FILES = 5
        _MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
        _MAX_TEXT_LEN = 4096

        async def bot_send_message_handler(request):
            """POST /bot/send-message — send message to user via MAX.

            Accepts both application/x-www-form-urlencoded (text-only) and
            multipart/form-data (with file attachments).
            Form fields: user_id (int), text (str, optional)
            Files: up to _MAX_FILES files, each up to _MAX_FILE_SIZE bytes.
            Auth: X-Internal-Token header must match INTERNAL_API_KEY env var.
            """
            token = request.headers.get("X-Internal-Token", "")
            expected_token = os.getenv("INTERNAL_API_KEY", "")
            if not expected_token or token != expected_token:
                return web.Response(status=403, text="Forbidden")

            try:
                content_type = request.content_type or ""
                user_id_str = None
                message_text = ""
                files_data: list[tuple[bytes, str]] = []

                if "multipart" in content_type:
                    # Multipart — used when sending file attachments
                    reader = await request.multipart()
                    async for part in reader:
                        name = part.name
                        if name == "user_id":
                            user_id_str = (await part.text()).strip()
                        elif name == "text":
                            message_text = await part.text()
                        elif part.filename:
                            data = await part.read()
                            if len(data) > _MAX_FILE_SIZE:
                                return web.Response(
                                    status=413,
                                    text=f"File too large (max {_MAX_FILE_SIZE // (1024*1024)}MB)",
                                )
                            files_data.append((data, part.filename))
                else:
                    # URL-encoded — used for text-only messages from backend
                    post = await request.post()
                    user_id_str = post.get("user_id", "").strip()
                    message_text = post.get("text", "")

                if not user_id_str:
                    return web.Response(status=400, text="user_id is required")

                try:
                    uid = int(user_id_str)
                except ValueError:
                    return web.Response(status=400, text="user_id must be integer")

                if len(files_data) > _MAX_FILES:
                    return web.Response(
                        status=413,
                        text=f"Too many files (max {_MAX_FILES})",
                    )

                message_text = message_text[:_MAX_TEXT_LEN]

                # Pre-upload files so monkey-patch doesn't try to serialize bytes
                upload_attachments = []
                if files_data:
                    from maxapi.types.input_media import InputMediaBuffer
                    for buf, name in files_data:
                        media = InputMediaBuffer(buf, filename=name)
                        uploaded = await bot.upload_media(media)
                        upload_attachments.append(uploaded)

                await bot.send_message(
                    user_id=uid,
                    text=message_text or None,
                    attachments=upload_attachments if upload_attachments else None,
                )

                return web.Response(
                    status=200,
                    content_type="application/json",
                    text='{"sent": true}',
                )

            except Exception as e:
                logger.exception("bot/send-message failed")
                return web.Response(status=500, text="Failed to send message")

        app.router.add_post("/bot/send-message", bot_send_message_handler)

        # Run with AppRunner (same as aio_webhook.run but we built app manually)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8080)
        await site.start()

        logger.info("Webhook server started on http://0.0.0.0:8080/")

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    finally:
        await shutdown_storage()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main())
