"""Зоны полива через ИИ-агента: сквозные проверки на том же стенде, что и
tests/test_watering_handlers.py (настоящий Dispatcher + фейковый Telegram),
но вместо реального AI API — подменённый ai_service.parse_intent, который
возвращает заранее заданный intent. Так проверяется всё, что делает бот
ПОСЛЕ разбора текста моделью: поиск зоны, изменения в БД, диалоги и тексты."""

from datetime import time, timedelta

import pytest

from bot.config import config
from bot.db import crud, database
from bot.services import ai_service
from bot.services import watering_service as ws
from bot.services.ai_service.cache import cache_key
from bot.services.ai_service.prompt import build_system_prompt
from tests.test_watering_handlers import TG_ID, Harness, _zones


class FakeAI:
    """Подмена ai_service.parse_intent: отдаёт заданный intent и запоминает,
    какие зоны бот передал модели в промпт."""

    def __init__(self) -> None:
        self.intent: dict = {}
        self.seen_zones: list[list[str]] = []

    async def parse_intent(self, text, existing_groups=None, existing_plants=None, user_id=None, existing_zones=None):
        self.seen_zones.append(list(existing_zones or []))
        return self.intent


@pytest.fixture
def ai(monkeypatch) -> FakeAI:
    fake = FakeAI()
    monkeypatch.setattr(config, "ai_enabled", True)
    monkeypatch.setattr(ai_service, "parse_intent", fake.parse_intent)
    return fake


async def ask(app: Harness, ai: FakeAI, text: str, **intent) -> None:
    """Пользователь пишет свободный текст, а «модель» понимает его как intent."""
    ai.intent = intent
    await app.say(text)


async def _make_zone(name: str, days: int = 7) -> int:
    """Создаёт зону напрямую в БД (в отличие от _create_zone из тестов меню,
    умеет несколько зон подряд)."""
    async with database.get_session() as session:
        user = await crud.get_or_create_user(session, TG_ID, None, None)
        zone = await ws.add_zone(session, user.id, name, days)
        return zone.id


async def _zone(name: str):
    return next(z for z in await _zones() if z.name == name)


# ---------- создание зоны ----------


async def test_add_zone_with_everything_named_creates_it_at_once(app, ai):
    await ask(
        app, ai, "создай зону Балкон, раз в 5 дней, напоминай в 9 утра",
        action="zone_add", zone_name="Балкон", interval_days=5, notify_time="09:00",
    )

    zone = await _zone("Балкон")
    assert zone.interval_days == 5
    # 09:00 — локальное (Минск, UTC+3), в БД лежит UTC
    assert zone.notify_time == time(6, 0)
    assert "✅ Зона «Балкон» добавлена" in app.tg.last_visible_text()
    assert "в <b>09:00</b> (Минск, UTC+3)" in app.tg.last_visible_text()


async def test_add_zone_without_interval_continues_in_regular_dialog(app, ai):
    await ask(app, ai, "создай зону Балкон", action="zone_add", zone_name="Балкон", interval_days=None, notify_time=None)
    assert "Как часто поливать зону «Балкон»" in app.tg.last_visible_text()
    assert await _zones() == []

    await app.say("3")  # дальше подхватывает обычный ZoneAdd-диалог
    assert "В какое время" in app.tg.last_visible_text()
    await app.press("wztime:0900")

    zone = await _zone("Балкон")
    assert (zone.interval_days, zone.notify_time) == (3, time(9, 0))


async def test_add_zone_without_time_asks_for_time_only(app, ai):
    await ask(app, ai, "зона Кухня раз в 4 дня", action="zone_add", zone_name="Кухня", interval_days=4, notify_time=None)
    assert "В какое время присылать напоминание про полив раз в 4 дн." in app.tg.last_visible_text()
    assert await _zones() == []

    await app.press("wztime:1200")
    zone = await _zone("Кухня")
    assert (zone.interval_days, zone.notify_time) == (4, time(12, 0))


async def test_add_zone_that_already_exists_changes_nothing(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "создай зону подоконник", action="zone_add", zone_name="подоконник", interval_days=2, notify_time="09:00")

    assert "уже есть" in app.tg.last_visible_text()
    (zone,) = await _zones()
    assert (zone.name, zone.interval_days) == ("Подоконник", 7)


@pytest.mark.parametrize("bad", [0, 366, "часто", 2.5, True])
async def test_add_zone_rejects_bad_interval(app, ai, bad):
    await ask(app, ai, "создай зону", action="zone_add", zone_name="Балкон", interval_days=bad, notify_time="09:00")

    assert "от 1 до 365" in app.tg.last_visible_text()
    assert await _zones() == []


async def test_add_zone_without_name_falls_back_to_generic_reply(app, ai):
    await ask(app, ai, "создай зону", action="zone_add", zone_name=None, interval_days=3)

    assert "Не совсем поняла" in app.tg.last_visible_text()


# ---------- полил ----------


async def test_water_marks_zone_watered_and_reschedules(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "полил подоконник", action="zone_water", zone_name="Подоконник")

    zone = await _zone("Подоконник")
    assert zone.last_watered_at is not None
    assert abs(zone.next_watering_at - (ws.utcnow() + timedelta(days=7))) < timedelta(seconds=30)
    assert "✅ Зона «Подоконник» полита. Следующий полив через 7 дн." in app.tg.last_visible_text()


async def test_water_finds_zone_despite_typo(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "полила подаконник", action="zone_water", zone_name="подаконник")

    assert (await _zone("Подоконник")).last_watered_at is not None


async def test_water_unknown_zone_lists_existing_ones(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "полил алоэ", action="zone_water", zone_name="Алоэ")

    assert "Не нашла зону «Алоэ»" in app.tg.last_visible_text()
    assert "«Подоконник»" in app.tg.last_visible_text()
    assert (await _zone("Подоконник")).last_watered_at is None


async def test_water_without_any_zones_hints_how_to_add(app, ai):
    await ask(app, ai, "полил балкон", action="zone_water", zone_name="Балкон")

    assert "зон полива пока нет" in app.tg.last_visible_text()


async def test_model_receives_current_zone_names(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "что-нибудь", action="unknown")

    assert ai.seen_zones[-1] == ["Подоконник"]


# ---------- отложить ----------


async def test_snooze_postpones_by_given_days(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "отложи подоконник на 3 дня", action="zone_snooze", zone_name="Подоконник", snooze_days=3)

    zone = await _zone("Подоконник")
    assert abs(zone.next_watering_at - (ws.utcnow() + timedelta(days=3))) < timedelta(seconds=30)
    assert zone.interval_days == 7
    assert "отложено на 3 дн." in app.tg.last_visible_text()


async def test_snooze_without_days_defaults_to_one_day(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "отложи подоконник", action="zone_snooze", zone_name="Подоконник", snooze_days=None)

    zone = await _zone("Подоконник")
    assert abs(zone.next_watering_at - (ws.utcnow() + timedelta(days=1))) < timedelta(seconds=30)
    assert "отложено на 1 дн." in app.tg.last_visible_text()


async def test_snooze_rejects_out_of_range_days(app, ai):
    await _make_zone("Подоконник", 7)
    before = (await _zone("Подоконник")).next_watering_at

    await ask(app, ai, "отложи на 999", action="zone_snooze", zone_name="Подоконник", snooze_days=999)

    assert "от 1 до 365" in app.tg.last_visible_text()
    assert (await _zone("Подоконник")).next_watering_at == before


# ---------- интервал ----------


async def test_change_interval(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "поливай подоконник раз в 3 дня", action="zone_interval", zone_name="Подоконник", interval_days=3)

    assert (await _zone("Подоконник")).interval_days == 3
    assert "теперь поливаем раз в 3 дн." in app.tg.last_visible_text()


async def test_change_interval_accepts_number_as_string(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "раз в 10 дней", action="zone_interval", zone_name="Подоконник", interval_days="10")

    assert (await _zone("Подоконник")).interval_days == 10


async def test_change_interval_without_days_falls_back_to_generic_reply(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "поменяй интервал", action="zone_interval", zone_name="Подоконник", interval_days=None)

    assert "Не совсем поняла" in app.tg.last_visible_text()
    assert (await _zone("Подоконник")).interval_days == 7


# ---------- время напоминания ----------


async def test_change_notify_time_converts_local_time_to_utc(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "напоминай в 8 вечера", action="zone_time", zone_name="Подоконник", notify_time="20:00")

    assert (await _zone("Подоконник")).notify_time == time(17, 0)
    assert "в <b>20:00</b> (Минск, UTC+3)" in app.tg.last_visible_text()


async def test_notify_time_early_morning_wraps_to_previous_day_in_utc(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "напоминай в 01:30", action="zone_time", zone_name="Подоконник", notify_time="01:30")

    assert (await _zone("Подоконник")).notify_time == time(22, 30)


async def test_clear_notify_time(app, ai):
    await _make_zone("Подоконник", 7)
    await ask(app, ai, "в 9", action="zone_time", zone_name="Подоконник", notify_time="09:00")
    assert (await _zone("Подоконник")).notify_time is not None

    await ask(app, ai, "убери время напоминания", action="zone_time", zone_name="Подоконник", notify_time="")

    assert (await _zone("Подоконник")).notify_time is None
    assert "фиксированный час напоминания убран" in app.tg.last_visible_text()


async def test_notify_time_garbage_is_rejected(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "утром", action="zone_time", zone_name="Подоконник", notify_time="утром")

    assert "Не поняла время" in app.tg.last_visible_text()
    assert (await _zone("Подоконник")).notify_time is None


# ---------- переименование ----------


async def test_rename_zone(app, ai):
    await _make_zone("Балкон", 7)

    await ask(app, ai, "переименуй зону Балкон в Лоджия", action="zone_rename", zone_name="Балкон", new_name="Лоджия")

    assert [z.name for z in await _zones()] == ["Лоджия"]
    assert "«Балкон» → «Лоджия»" in app.tg.last_visible_text()


async def test_rename_zone_to_existing_name_is_refused(app, ai):
    await _make_zone("Балкон", 7)
    await _make_zone("Кухня", 3)

    await ask(app, ai, "переименуй", action="zone_rename", zone_name="Балкон", new_name="кухня")

    assert "уже есть" in app.tg.last_visible_text()
    assert sorted(z.name for z in await _zones()) == ["Балкон", "Кухня"]


# ---------- удаление ----------


async def test_delete_zone_asks_confirmation_first(app, ai):
    await _make_zone("Балкон", 7)

    await ask(app, ai, "удали зону Балкон", action="zone_delete", zone_name="Балкон")

    assert "Удалить зону «Балкон»" in app.tg.last_visible_text()
    assert app.tg.last_markup_callbacks() == ["aizdelconfirm", "aizcancel"]
    assert len(await _zones()) == 1

    await app.press("aizdelconfirm")

    assert await _zones() == []
    assert "Удалила зону «Балкон»" in app.tg.last_visible_text()


async def test_delete_zone_can_be_cancelled(app, ai):
    await _make_zone("Балкон", 7)
    await ask(app, ai, "удали зону Балкон", action="zone_delete", zone_name="Балкон")

    await app.press("aizcancel")

    assert len(await _zones()) == 1


# ---------- показ ----------


async def test_list_without_name_shows_overview(app, ai):
    await _make_zone("Балкон", 7)
    await _make_zone("Кухня", 3)

    await ask(app, ai, "что пора поливать", action="zone_list", zone_name=None)

    text = app.tg.last_visible_text()
    assert "Зоны полива" in text
    assert "Балкон" in text
    assert "Кухня" in text
    assert "wzadd" in app.tg.last_markup_callbacks()


async def test_list_with_name_shows_zone_card(app, ai):
    zone_id = await _make_zone("Балкон", 7)

    await ask(app, ai, "как там балкон", action="zone_list", zone_name="Балкон")

    assert "Поливать: раз в 7 дн." in app.tg.last_visible_text()
    assert f"wzwaterask:{zone_id}" in app.tg.last_markup_callbacks()


async def test_list_without_zones_says_so(app, ai):
    await ask(app, ai, "покажи зоны", action="zone_list", zone_name=None)

    assert "Пока нет ни одной зоны" in app.tg.last_visible_text()


# ---------- несколько подходящих зон ----------


async def test_several_matching_zones_ask_which_one(app, ai):
    await _make_zone("Балкон большой", 7)
    await _make_zone("Балкон малый", 5)

    await ask(app, ai, "полил балкон", action="zone_water", zone_name="Балкон")

    assert "Нашла несколько зон «Балкон»" in app.tg.last_visible_text()
    big = await _zone("Балкон большой")
    small = await _zone("Балкон малый")
    callbacks = app.tg.last_markup_callbacks()
    assert sorted(callbacks) == sorted([f"aizpick:{big.id}", f"aizpick:{small.id}", "aizcancel"])

    await app.press(f"aizpick:{small.id}")

    assert (await _zone("Балкон малый")).last_watered_at is not None
    assert (await _zone("Балкон большой")).last_watered_at is None


async def test_delete_after_pick_still_asks_confirmation(app, ai):
    await _make_zone("Балкон большой", 7)
    await _make_zone("Балкон малый", 5)
    await ask(app, ai, "удали балкон", action="zone_delete", zone_name="Балкон")
    small = await _zone("Балкон малый")

    await app.press(f"aizpick:{small.id}")
    assert "Удалить зону «Балкон малый»" in app.tg.last_visible_text()
    assert len(await _zones()) == 2

    await app.press("aizdelconfirm")
    assert [z.name for z in await _zones()] == ["Балкон большой"]


# ---------- имена зон не ломают HTML-разметку сообщений ----------


async def test_zone_names_are_html_escaped_in_replies(app, ai):
    await _make_zone("A<b>B", 7)

    await ask(app, ai, "полил", action="zone_water", zone_name="A<b>B")

    assert "«A&lt;b&gt;B»" in app.tg.last_visible_text()


# ---------- лог ИИ-агента ----------


async def test_ai_log_records_zone_name(app, ai):
    await _make_zone("Подоконник", 7)

    await ask(app, ai, "полил подоконник", action="zone_water", zone_name="Подоконник")

    async with database.get_session() as session:
        user = await crud.get_or_create_user(session, TG_ID, None, None)
        (log,) = await crud.list_ai_logs_for_user(session, user.id)
    assert (log.action, log.zone_name) == ("zone_water", "Подоконник")


# ---------- промпт, кэш, время ----------


def test_prompt_lists_zone_names_and_all_zone_actions():
    prompt = build_system_prompt(None, None, ["Подоконник", "Балкон"])

    assert "«Подоконник»" in prompt
    assert "«Балкон»" in prompt
    for action in [
        "zone_add", "zone_water", "zone_snooze", "zone_interval", "zone_time", "zone_rename", "zone_delete", "zone_list",
    ]:
        assert f'action="{action}"' in prompt


def test_prompt_without_zones_says_there_are_none():
    assert "нет ни одной зоны полива" in build_system_prompt(None, None, None)
    assert "нет ни одной зоны полива" in build_system_prompt(None, None)


def test_cache_key_depends_on_zone_names():
    assert cache_key(1, "полил", None, None, ["А"]) != cache_key(1, "полил", None, None, ["Б"])
    assert cache_key(1, "полил", None, None) == cache_key(1, "полил", None, None, None)


@pytest.mark.parametrize(
    ("local", "utc"),
    [(time(9, 0), time(6, 0)), (time(0, 0), time(21, 0)), (time(1, 30), time(22, 30)), (time(23, 59), time(20, 59))],
)
def test_from_display_time_is_inverse_of_to_display_time(local, utc):
    assert ws.from_display_time(local) == utc
    assert ws.to_display_time(utc) == local


async def test_migration_adds_zone_name_column_to_existing_ai_logs_table():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from bot.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("ALTER TABLE ai_logs DROP COLUMN zone_name")  # как в БД до этой фичи
        await database._migrate_add_missing_columns(conn)
        columns = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info(ai_logs)")).fetchall()}
    await engine.dispose()

    assert "zone_name" in columns
