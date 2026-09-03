import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import config
from bot.db.database import init_db
from bot.handlers import ai_agent, groups, import_, list_view, plants
from bot.middlewares.user import UserMiddleware

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    await init_db()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # резолвит/создаёт пользователя БД для каждого апдейта и подставляет
    # user_id в хендлеры — до роутеров, поэтому применяется ко всем
    dp.update.outer_middleware(UserMiddleware())

    # порядок важен: конкретные команды/FSM-сценарии — раньше,
    # свободный ИИ-агент — последним, чтобы не перехватывать чужие сообщения
    dp.include_router(list_view.router)
    dp.include_router(plants.router)
    dp.include_router(groups.router)
    dp.include_router(import_.router)
    dp.include_router(ai_agent.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
