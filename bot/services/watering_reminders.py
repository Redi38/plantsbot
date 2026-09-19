"""Фоновая рассылка напоминаний о поливе.

Запускается из bot.main как обычная asyncio-задача рядом с polling'ом:
раз в минуту берёт из БД зоны, у которых срок наступил и по этому циклу
ещё не напоминали, и шлёт владельцу сообщение с кнопками «Полил» /
«Отложить». Отдельные процессы, cron и очередь для этого не нужны — бот и
так один и держит соединение с БД.

Отправка вынесена в send_due_reminders(session) отдельно от бесконечного
цикла — так её можно проверить тестом, не крутя таймеры."""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.watering import reminder_keyboard
from bot.services.watering_service import render_reminder, utcnow

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


async def send_due_reminders(bot: Bot, session: AsyncSession, now: datetime | None = None) -> int:
    """Отправляет все созревшие напоминания, возвращает сколько ушло.

    После каждой отправки notified_at коммитится сразу, а не пачкой в
    конце: если процесс упадёт посреди рассылки, уже отправленные
    напоминания не повторятся после перезапуска.

    Если пользователь заблокировал бота (TelegramForbiddenError), всё
    равно помечаем зону как «уведомлённую» — иначе мы бы стучались к нему
    каждую минуту вечно. Прочие ошибки Telegram (сеть, лимиты) считаем
    временными: оставляем зону как есть, повторим на следующем проходе."""
    now = now or utcnow()
    sent = 0
    for zone, chat_id in await crud.list_due_zones(session, now):
        try:
            await bot.send_message(chat_id, render_reminder(zone), reply_markup=reminder_keyboard(zone.id))
        except TelegramForbiddenError:
            logger.info("Пользователь %s заблокировал бота — напоминание о зоне %s пропущено", chat_id, zone.id)
        except TelegramAPIError:
            logger.exception("Не удалось отправить напоминание о зоне %s, повторю на следующем проходе", zone.id)
            continue
        else:
            sent += 1
        zone.notified_at = now
        await session.commit()
    return sent


async def run_reminder_loop(bot: Bot, interval_seconds: int = CHECK_INTERVAL_SECONDS) -> None:
    """Бесконечный цикл проверки. Любое исключение внутри прохода
    логируется и не роняет цикл — иначе одна временная ошибка БД тихо
    останавливала бы напоминания до ближайшего перезапуска бота.

    Первый проход выполняется сразу при старте: напоминания, которые
    «созрели» пока бот был выключен, придут, как только он поднимется."""
    while True:
        try:
            async with get_session() as session:
                await send_due_reminders(bot, session)
        except Exception:
            logger.exception("Ошибка при рассылке напоминаний о поливе")
        await asyncio.sleep(interval_seconds)
