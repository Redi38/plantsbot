"""Фоновая рассылка напоминаний о сроке годности препаратов для растений.

Работает по тому же принципу, что и напоминания о поливе
(bot/services/watering_reminders.py): отдельная asyncio-задача рядом с
polling'ом раз в минуту ищет препараты, до конца срока годности которых
осталось не больше NOTIFY_DAYS_BEFORE дней и про которые ещё не напоминали.

Напоминание одноразовое: после отправки ставим notified_at. Препарат,
добавленный уже внутри 30-дневного окна (или с просроченной датой),
получит напоминание сразу, в ближайшее разрешённое время суток."""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.medicine import reminder_keyboard
from bot.services.medicine_service import NOTIFY_DAYS_BEFORE, NOTIFY_HOUR_UTC, render_reminder, utcnow

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


async def send_due_reminders(bot: Bot, session: AsyncSession, now: datetime | None = None) -> int:
    """Отправляет все созревшие напоминания, возвращает сколько ушло.

    До NOTIFY_HOUR_UTC ничего не шлём — иначе напоминание прилетало бы в
    полночь. Как и в поливе, notified_at коммитится после каждой отправки, а
    пользователь, заблокировавший бота (TelegramForbiddenError), всё равно
    помечается «уведомлённым», чтобы не стучаться к нему каждую минуту.
    Прочие ошибки Telegram считаем временными и повторяем на следующем проходе."""
    now = now or utcnow()
    if now.hour < NOTIFY_HOUR_UTC:
        return 0
    today = now.date()
    sent = 0
    for medicine, chat_id in await crud.list_due_medicines(session, today, NOTIFY_DAYS_BEFORE):
        try:
            await bot.send_message(
                chat_id, render_reminder(medicine, today), reply_markup=reminder_keyboard(medicine.id)
            )
        except TelegramForbiddenError:
            logger.info("Пользователь %s заблокировал бота — напоминание о препарате %s пропущено", chat_id, medicine.id)
        except TelegramAPIError:
            logger.exception("Не удалось отправить напоминание о препарате %s, повторю на следующем проходе", medicine.id)
            continue
        else:
            sent += 1
        medicine.notified_at = now
        await session.commit()
    return sent


async def run_reminder_loop(bot: Bot, interval_seconds: int = CHECK_INTERVAL_SECONDS) -> None:
    """Бесконечный цикл проверки; исключение внутри прохода логируется и не
    роняет цикл. Первый проход — сразу при старте, чтобы напоминания,
    «созревшие» пока бот был выключен, пришли, как только он поднимется."""
    while True:
        try:
            async with get_session() as session:
                await send_due_reminders(bot, session)
        except Exception:
            logger.exception("Ошибка при рассылке напоминаний о сроке годности")
        await asyncio.sleep(interval_seconds)
