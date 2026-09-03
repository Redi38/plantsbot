"""
Мидлварь, резолвящая пользователя БД для каждого апдейта.

Раньше каждый хендлер сам открывал сессию и звал
crud.get_or_create_user(session, from_user.id, from_user.username, from_user.full_name)
— один и тот же блок из 3 строк повторялся ~18 раз по всем хендлерам.
Теперь это делается один раз здесь, а хендлер просто объявляет параметр
user_id: int (или user: User, если нужен весь объект) — aiogram сам
подставит его из data при вызове.

Регистрируется как dp.update.outer_middleware — выполняется раньше
роутеров, для любых типов апдейтов, у которых есть отправитель
(Message, CallbackQuery и т.п.); event_from_user на этот момент уже
заполнен встроенной UserContextMiddleware aiogram.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db import crud
from bot.db.database import get_session


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = data.get("event_from_user")
        if from_user is not None:
            async with get_session() as session:
                user = await crud.get_or_create_user(
                    session, from_user.id, from_user.username, from_user.full_name
                )
                await session.commit()
            data["user_id"] = user.id

        return await handler(event, data)
