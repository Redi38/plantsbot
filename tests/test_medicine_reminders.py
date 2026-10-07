from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.methods import SendMessage

from bot.db import crud
from bot.services import medicine_service as ms
from bot.services.medicine_reminders import send_due_reminders

TELEGRAM_ID = 123456789  # см. фикстуру user_id в conftest
NOW = datetime(2026, 10, 6, 12, 0)  # после NOTIFY_HOUR_UTC


def _api_error(cls):
    return cls(method=SendMessage(chat_id=TELEGRAM_ID, text="x"), message="boom")


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock()


async def test_reminds_a_month_before_expiry(session, user_id, bot):
    medicine = await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=30))

    assert await send_due_reminders(bot, session, now=NOW) == 1

    args, kwargs = bot.send_message.await_args
    assert args[0] == TELEGRAM_ID
    assert "Актара" in args[1]
    assert "осталось 30 дн." in args[1]
    assert _callbacks(kwargs["reply_markup"]) == [f"medtrash:{medicine.id}", "medok"]


async def test_does_not_remind_earlier_than_a_month(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=31))

    assert await send_due_reminders(bot, session, now=NOW) == 0
    bot.send_message.assert_not_awaited()


async def test_medicine_without_expiry_never_triggers(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Фундазол", "Фунгицид")

    assert await send_due_reminders(bot, session, now=NOW) == 0


async def test_reminder_is_sent_once(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=10))

    assert await send_due_reminders(bot, session, now=NOW) == 1
    assert await send_due_reminders(bot, session, now=NOW + timedelta(minutes=1)) == 0
    assert await send_due_reminders(bot, session, now=NOW + timedelta(days=5)) == 0


async def test_already_expired_medicine_gets_a_reminder(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=date(2026, 9, 1))

    assert await send_due_reminders(bot, session, now=NOW) == 1
    assert "истёк" in bot.send_message.await_args.args[1]


async def test_nothing_is_sent_at_night(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=5))
    night = datetime(2026, 10, 6, 2, 0)

    assert await send_due_reminders(bot, session, now=night) == 0
    assert await send_due_reminders(bot, session, now=night.replace(hour=ms.NOTIFY_HOUR_UTC)) == 1


async def test_blocked_user_is_marked_notified(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=5))
    bot.send_message.side_effect = _api_error(TelegramForbiddenError)

    assert await send_due_reminders(bot, session, now=NOW) == 0
    bot.send_message.side_effect = None
    assert await send_due_reminders(bot, session, now=NOW + timedelta(minutes=1)) == 0
    bot.send_message.assert_awaited_once()


async def test_temporary_telegram_error_is_retried(session, user_id, bot):
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date() + timedelta(days=5))
    bot.send_message.side_effect = _api_error(TelegramAPIError)

    assert await send_due_reminders(bot, session, now=NOW) == 0
    bot.send_message.side_effect = None
    assert await send_due_reminders(bot, session, now=NOW + timedelta(minutes=1)) == 1


async def test_due_list_is_per_owner(session, user_id):
    other = await crud.get_or_create_user(session, telegram_id=555, username=None, full_name=None)
    await session.commit()
    await ms.add_medicine(session, user_id, "Актара", "Инсектицид", expires_at=NOW.date())
    await ms.add_medicine(session, other.id, "Фундазол", "Фунгицид", expires_at=NOW.date())

    due = await crud.list_due_medicines(session, NOW.date(), ms.NOTIFY_DAYS_BEFORE)

    assert sorted((m.name, tg) for m, tg in due) == [("Актара", TELEGRAM_ID), ("Фундазол", 555)]
