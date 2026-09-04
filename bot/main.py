import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from bot.config import config
from bot.db.database import init_db
from bot.handlers import ai_agent, groups, import_, list_view, plants
from bot.middlewares.user import UserMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        """Ловит необработанные исключения из любого хендлера, чтобы падение
        одного апдейта не проходило молча — раньше пользователь просто не
        получал ответа и не понимал, что что-то пошло не так, а трасса
        падения терялась в логах uvicorn/aiogram без контекста."""
        logger.exception(
            "Необработанная ошибка при обработке апдейта %s", event.update.update_id,
            exc_info=event.exception,
        )
        chat_id = None
        if event.update.message:
            chat_id = event.update.message.chat.id
        elif event.update.callback_query and event.update.callback_query.message:
            chat_id = event.update.callback_query.message.chat.id
        if chat_id:
            try:
                await bot.send_message(chat_id, "⚠️ Что-то пошло не так. Попробуй ещё раз.")
            except Exception:
                logger.exception("Не удалось отправить сообщение об ошибке пользователю")
        return True

    dp.update.outer_middleware(UserMiddleware())

    dp.include_router(list_view.router)
    dp.include_router(plants.router)
    dp.include_router(groups.router)
    dp.include_router(import_.router)
    dp.include_router(ai_agent.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
