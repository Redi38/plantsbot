from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.methods import SendMessage

from bot.db import crud
from bot.keyboards import watering as kb
from bot.services import watering_service as ws
from bot.services.watering_reminders import send_due_reminders

NOW = datetime(2026, 9, 1, 12, 0)
TELEGRAM_ID = 123456789  # см. фикстуру user_id в conftest


def _api_error(cls):
    return cls(method=SendMessage(chat_id=TELEGRAM_ID, text="x"), message="boom")


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock()


async def test_sends_reminder_with_watered_and_snooze_buttons(session, user_id, bot):
    zone = await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)

    sent = await send_due_reminders(bot, session, now=NOW + timedelta(days=3))

    assert sent == 1
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args[0] == TELEGRAM_ID
    assert "Пора полить зону «Подоконник»" in args[1]
    assert _callbacks(kwargs["reply_markup"]) == [
        f"wzdone:{zone.id}",
        f"wzlate:{zone.id}:1",
        f"wzlate:{zone.id}:2",
        f"wzlate:{zone.id}:3",
    ]


async def test_does_nothing_before_the_deadline(session, user_id, bot):
    await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)

    assert await send_due_reminders(bot, session, now=NOW + timedelta(days=2)) == 0
    bot.send_message.assert_not_awaited()


async def test_does_not_repeat_reminder_on_next_pass(session, user_id, bot):
    await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)
    due = NOW + timedelta(days=3)

    assert await send_due_reminders(bot, session, now=due) == 1
    assert await send_due_reminders(bot, session, now=due + timedelta(minutes=1)) == 0
    assert bot.send_message.await_count == 1


async def test_reminds_again_when_snooze_expires(session, user_id, bot):
    zone = await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)
    due = NOW + timedelta(days=3)
    await send_due_reminders(bot, session, now=due)

    await ws.snooze(session, zone, 1, now=due)

    assert await send_due_reminders(bot, session, now=due + timedelta(hours=23)) == 0
    assert await send_due_reminders(bot, session, now=due + timedelta(days=1)) == 1
    assert bot.send_message.await_count == 2


async def test_next_cycle_reminds_after_watering(session, user_id, bot):
    zone = await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)
    due = NOW + timedelta(days=3)
    await send_due_reminders(bot, session, now=due)

    await ws.mark_watered(session, zone, now=due + timedelta(hours=1))

    assert await send_due_reminders(bot, session, now=due + timedelta(days=2)) == 0
    assert await send_due_reminders(bot, session, now=due + timedelta(days=3, hours=1)) == 1


async def test_blocked_bot_is_marked_notified_and_not_retried(session, user_id, bot):
    await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)
    bot.send_message.side_effect = _api_error(TelegramForbiddenError)
    due = NOW + timedelta(days=3)

    assert await send_due_reminders(bot, session, now=due) == 0
    assert await send_due_reminders(bot, session, now=due + timedelta(minutes=1)) == 0
    assert bot.send_message.await_count == 1


async def test_temporary_telegram_error_is_retried_on_next_pass(session, user_id, bot):
    await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)
    due = NOW + timedelta(days=3)

    bot.send_message.side_effect = _api_error(TelegramAPIError)
    assert await send_due_reminders(bot, session, now=due) == 0

    bot.send_message.side_effect = None
    assert await send_due_reminders(bot, session, now=due + timedelta(minutes=1)) == 1


async def test_one_failing_user_does_not_block_others(session, user_id, bot):
    other = await crud.get_or_create_user(session, telegram_id=999, username=None, full_name=None)
    await session.commit()
    await ws.add_zone(session, user_id, "Первая", 3, now=NOW)
    await ws.add_zone(session, other.id, "Вторая", 3, now=NOW + timedelta(seconds=1))

    async def flaky(chat_id, *args, **kwargs):
        if chat_id == TELEGRAM_ID:
            raise _api_error(TelegramAPIError)

    bot.send_message.side_effect = flaky

    assert await send_due_reminders(bot, session, now=NOW + timedelta(days=4)) == 1
    assert {call.args[0] for call in bot.send_message.await_args_list} == {TELEGRAM_ID, 999}


# ---------- клавиатуры ----------


async def test_all_callback_data_fit_telegram_limit(session, user_id):
    zone = await ws.add_zone(session, user_id, "Х" * 100, 7, now=NOW)
    markups = [
        kb.zones_menu_keyboard([zone], NOW),
        kb.zone_card_keyboard(10**9),
        kb.interval_keyboard(f"wzeint:{10**9}", f"wz:{10**9}"),
        kb.reminder_keyboard(10**9),
    ]

    for markup in markups:
        for data in _callbacks(markup):
            assert len(data.encode()) <= 64, data


async def test_zones_menu_marks_due_zones(session, user_id):
    due = await ws.add_zone(session, user_id, "Пора", 1, now=NOW)
    await ws.add_zone(session, user_id, "Рано", 10, now=NOW)
    zones = await crud.list_zones(session, user_id)

    markup = kb.zones_menu_keyboard(zones, NOW + timedelta(days=1))

    texts = [button.text for row in markup.inline_keyboard for button in row]
    assert texts[:3] == ["➕ Добавить зону", "🔔 Пора", "💧 Рано"]
    assert f"wz:{due.id}" in _callbacks(markup)
