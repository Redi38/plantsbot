"""Сквозные проверки хендлеров зон полива: настоящий Dispatcher с теми же
роутерами и мидлварью, что в bot.main, но вместо сети — фейковая сессия,
которая записывает все вызовы Bot API. Так проверяется то, что юнит-тесты
сервиса не видят: роутинг, FSM-диалог создания, колбэки кнопок."""

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import Chat, Message
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db import crud, database
from bot.db.models import Base
from bot.handlers import ai_agent, groups, import_, list_view, plants, watering
from bot.keyboards.reply import BTN_LIST, BTN_WATER
from bot.middlewares.user import UserMiddleware
from bot.services import watering_service as ws
from bot.services.watering_reminders import send_due_reminders

TG_ID = 4242


class FakeTelegram(BaseSession):
    """Записывает вызовы и отвечает так, как ответил бы Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self._next_message_id = 100

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs) -> AsyncIterator[bytes]:
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_message_id += 1
            return Message(
                message_id=self._next_message_id,
                date=datetime.now(),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True

    def sent_texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage)]

    def edited_texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, EditMessageText)]

    def last_visible_text(self) -> str:
        """Текст последнего SendMessage/EditMessageText."""
        texts = [c.text for c in self.calls if isinstance(c, SendMessage | EditMessageText)]
        return texts[-1]

    def last_markup_callbacks(self) -> list[str]:
        for call in reversed(self.calls):
            if isinstance(call, SendMessage | EditMessageText) and call.reply_markup:
                return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]
        return []


class Harness:
    def __init__(self, dp: Dispatcher, bot: Bot, tg: FakeTelegram) -> None:
        self.dp, self.bot, self.tg = dp, bot, tg
        self._update_id = 0
        self._menu_message_id = 100  # id сообщения, под которым «нажимаются» кнопки

    def _next(self) -> int:
        self._update_id += 1
        return self._update_id

    async def say(self, text: str) -> None:
        n = self._next()
        await self.dp.feed_raw_update(
            self.bot,
            {
                "update_id": n,
                "message": {
                    "message_id": 1000 + n,
                    "date": int(datetime.now().timestamp()),
                    "chat": {"id": TG_ID, "type": "private"},
                    "from": {"id": TG_ID, "is_bot": False, "first_name": "Tester", "username": "tester"},
                    "text": text,
                },
            },
        )

    async def press(self, data: str, message_id: int | None = None) -> None:
        n = self._next()
        await self.dp.feed_raw_update(
            self.bot,
            {
                "update_id": n,
                "callback_query": {
                    "id": str(n),
                    "chat_instance": "ci",
                    "from": {"id": TG_ID, "is_bot": False, "first_name": "Tester", "username": "tester"},
                    "data": data,
                    "message": {
                        "message_id": message_id or self._menu_message_id,
                        "date": int(datetime.now().timestamp()),
                        "chat": {"id": TG_ID, "type": "private"},
                        "text": "старое сообщение",
                    },
                },
            },
        )


_ROUTERS = (list_view.router, plants.router, groups.router, import_.router, watering.router, ai_agent.router)


@pytest.fixture(scope="module")
def dispatcher() -> Iterator[Dispatcher]:
    """Роутеры проекта — модульные синглтоны, а aiogram позволяет прикрепить
    роутер только к одному диспетчеру. Поэтому диспетчер один на модуль
    (тот же порядок роутеров и та же мидлварь, что в bot.main), а по
    окончании роутеры отцепляются, чтобы не влиять на другие тесты."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UserMiddleware())
    for router in _ROUTERS:
        dp.include_router(router)
    yield dp
    for router in _ROUTERS:
        router._parent_router = None


@pytest_asyncio.fixture
async def app(monkeypatch, dispatcher) -> AsyncIterator[Harness]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # и middleware, и хендлеры ходят в БД через bot.db.database.get_session
    monkeypatch.setattr(database, "async_session", async_sessionmaker(engine, expire_on_commit=False))
    dispatcher.storage.storage.clear()  # чистое FSM-состояние на каждый тест

    tg = FakeTelegram()
    yield Harness(dispatcher, Bot("123456:TEST", session=tg), tg)
    await engine.dispose()


async def _zones():
    async with database.get_session() as session:
        user = await crud.get_or_create_user(session, TG_ID, None, None)
        return await crud.list_zones(session, user.id)


async def _create_zone(app: Harness, name: str = "Подоконник", days: int = 7) -> int:
    await app.say(BTN_WATER)
    await app.press("wzadd")
    await app.say(name)
    await app.say(str(days))
    (zone,) = await _zones()
    return zone.id


async def test_water_button_shows_empty_menu_with_add_button(app):
    await app.say(BTN_WATER)

    assert "Пока нет ни одной зоны" in app.tg.last_visible_text()
    assert "wzadd" in app.tg.last_markup_callbacks()


async def test_full_add_flow_creates_zone(app):
    await app.say(BTN_WATER)
    await app.press("wzadd")
    assert "Как назвать зону" in app.tg.last_visible_text()

    await app.say("Подоконник")
    assert "Как часто поливать зону «Подоконник»" in app.tg.last_visible_text()

    await app.say("7")
    assert "✅ Зона «Подоконник» добавлена" in app.tg.last_visible_text()
    assert "через 7 дн." in app.tg.last_visible_text()

    (zone,) = await _zones()
    assert (zone.name, zone.interval_days) == ("Подоконник", 7)


async def test_interval_can_be_picked_with_preset_button(app):
    await app.say(BTN_WATER)
    await app.press("wzadd")
    await app.say("Балкон")

    await app.press("wzint:14")

    (zone,) = await _zones()
    assert (zone.name, zone.interval_days) == ("Балкон", 14)
    assert "✅ Зона «Балкон» добавлена" in app.tg.last_visible_text()


async def test_invalid_interval_is_rejected_and_dialog_continues(app):
    await app.say(BTN_WATER)
    await app.press("wzadd")
    await app.say("Балкон")

    await app.say("часто")
    assert "Нужно число дней" in app.tg.last_visible_text()
    assert await _zones() == []

    await app.say("5")
    (zone,) = await _zones()
    assert zone.interval_days == 5


async def test_duplicate_name_is_rejected_at_name_step(app):
    await _create_zone(app, "Подоконник")

    await app.say(BTN_WATER)
    await app.press("wzadd")
    await app.say("подоконник")

    assert "уже есть" in app.tg.last_visible_text()
    assert len(await _zones()) == 1


async def test_cancel_leaves_dialog_and_creates_nothing(app):
    await app.say(BTN_WATER)
    await app.press("wzadd")
    await app.say("Балкон")

    await app.press("wzcancel")
    await app.say("7")  # больше не ответ на вопрос про интервал

    assert await _zones() == []


async def test_menu_button_during_dialog_aborts_it(app):
    await app.say(BTN_WATER)
    await app.press("wzadd")

    await app.say(BTN_LIST)  # уходим в другой раздел на шаге ввода названия
    await app.say("Балкон")  # не должно стать названием зоны

    assert await _zones() == []


async def test_html_in_zone_name_is_escaped_in_messages(app):
    await _create_zone(app, "<b>x</b>")

    assert "<b>x</b>" not in app.tg.last_visible_text()
    assert "&lt;b&gt;x&lt;/b&gt;" in app.tg.last_visible_text()


async def test_zone_card_shows_interval_and_water_button_records_watering(app):
    zone_id = await _create_zone(app, "Подоконник", 7)

    await app.press(f"wz:{zone_id}")
    assert "Поливать: раз в 7 дн." in app.tg.last_visible_text()
    assert "Последний полив: ещё не отмечено" in app.tg.last_visible_text()

    await app.press(f"wzwater:{zone_id}")
    assert "✅ Полив записан" in app.tg.last_visible_text()
    assert "Последний полив: сегодня" in app.tg.last_visible_text()
    (zone,) = await _zones()
    assert zone.last_watered_at is not None


async def test_change_interval_by_button_and_by_text(app):
    zone_id = await _create_zone(app, "Подоконник", 7)

    await app.press(f"wzedit:{zone_id}")
    await app.press(f"wzeint:{zone_id}:3")
    assert "Теперь поливаем раз в 3 дн." in app.tg.last_visible_text()
    assert (await _zones())[0].interval_days == 3

    await app.press(f"wzedit:{zone_id}")
    await app.say("10 дней")
    assert "Теперь поливаем раз в 10 дн." in app.tg.last_visible_text()
    assert (await _zones())[0].interval_days == 10


async def test_delete_asks_confirmation_then_removes(app):
    zone_id = await _create_zone(app)

    await app.press(f"wzdel:{zone_id}")
    assert "Удалить зону «Подоконник»" in app.tg.last_visible_text()
    assert len(await _zones()) == 1  # пока только спросили

    await app.press(f"wzdelc:{zone_id}")
    assert await _zones() == []
    assert "удалена" in app.tg.last_visible_text()


async def test_reminder_flow_done_button(app):
    zone_id = await _create_zone(app, "Подоконник", 3)
    async with database.get_session() as session:
        assert await send_due_reminders(app.bot, session, now=ws.utcnow() + timedelta(days=3)) == 1
    assert "Пора полить зону «Подоконник»" in app.tg.last_visible_text()
    assert app.tg.last_markup_callbacks() == [
        f"wzdone:{zone_id}",
        f"wzlate:{zone_id}:1",
        f"wzlate:{zone_id}:2",
        f"wzlate:{zone_id}:3",
    ]

    await app.press(f"wzdone:{zone_id}")

    assert "Зона «Подоконник» полита. Следующий полив через 3 дн." in app.tg.last_visible_text()
    (zone,) = await _zones()
    assert zone.last_watered_at is not None
    assert zone.next_watering_at > ws.utcnow() + timedelta(days=2, hours=23)


async def test_reminder_flow_snooze_button(app):
    zone_id = await _create_zone(app, "Подоконник", 3)
    before = ws.utcnow()

    await app.press(f"wzlate:{zone_id}:2")

    assert "Отложено на 2 дн." in app.tg.last_visible_text()
    (zone,) = await _zones()
    assert before + timedelta(days=2) <= zone.next_watering_at <= ws.utcnow() + timedelta(days=2)
    assert zone.interval_days == 3  # интервал не меняется


async def test_snooze_rejects_days_not_offered_by_keyboard(app):
    zone_id = await _create_zone(app, "Подоконник", 3)
    original = (await _zones())[0].next_watering_at

    await app.press(f"wzlate:{zone_id}:99")

    assert (await _zones())[0].next_watering_at == original


async def test_buttons_of_deleted_zone_do_not_crash(app):
    zone_id = await _create_zone(app)
    await app.press(f"wzdelc:{zone_id}")
    calls_before = len(app.tg.calls)

    for data in (f"wz:{zone_id}", f"wzwater:{zone_id}", f"wzdone:{zone_id}", f"wzlate:{zone_id}:1", f"wzedit:{zone_id}"):
        await app.press(data)

    assert len(app.tg.calls) > calls_before  # бот ответил, а не упал молча
    assert not any(isinstance(c, SendMessage) and "Что-то пошло не так" in c.text for c in app.tg.calls)
