from datetime import datetime, time, timedelta

import pytest

from bot.db import crud
from bot.services import watering_service as ws

NOW = datetime(2026, 9, 1, 12, 0)


# ---------- parse_interval ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", 7),
        (" 14 ", 14),
        ("10 дней", 10),
        ("3д", 3),
        ("1 день", 1),
        ("5 days", 5),
        ("365", 365),
    ],
)
def test_parse_interval_accepts_number_of_days(text, expected):
    assert ws.parse_interval(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-3", "366", "7.5", "раз в неделю", "1 2"])
def test_parse_interval_rejects_garbage_and_out_of_range(text):
    assert ws.parse_interval(text) is None


# ---------- создание зоны ----------


async def test_add_zone_schedules_first_reminder_after_interval(session, user_id):
    zone = await ws.add_zone(session, user_id, "  Подоконник ", 7, now=NOW)

    assert zone.name == "Подоконник"
    assert zone.interval_days == 7
    assert zone.next_watering_at == NOW + timedelta(days=7)
    assert zone.last_watered_at is None
    assert zone.notified_at is None
    assert zone.notify_time is None


# ---------- время напоминания ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9:00", time(9, 0)),
        ("09:00", time(9, 0)),
        (" 21:15 ", time(21, 15)),
        ("21.15", time(21, 15)),
        ("0:00", time(0, 0)),
        ("23:59", time(23, 59)),
    ],
)
def test_parse_notify_time_accepts_hh_mm(text, expected):
    assert ws.parse_notify_time(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "24:00", "9:60", "9", "9-00"])
def test_parse_notify_time_rejects_garbage(text):
    assert ws.parse_notify_time(text) is None


async def test_add_zone_with_notify_time_pins_hour_on_target_date(session, user_id):
    zone = await ws.add_zone(session, user_id, "Подоконник", 7, time(9, 0), now=NOW)

    assert zone.notify_time == time(9, 0)
    assert zone.next_watering_at == datetime.combine((NOW + timedelta(days=7)).date(), time(9, 0))


async def test_mark_watered_respects_existing_notify_time(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 5, time(9, 0), now=NOW)

    watered_at = NOW + timedelta(days=6, hours=3)
    await ws.mark_watered(session, zone, now=watered_at)

    assert zone.next_watering_at == datetime.combine((watered_at + timedelta(days=5)).date(), time(9, 0))


async def test_set_notify_time_changes_hour_without_shifting_day(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)
    original_day = zone.next_watering_at.date()

    await ws.set_notify_time(session, zone, time(18, 30))

    assert zone.notify_time == time(18, 30)
    assert zone.next_watering_at == datetime.combine(original_day, time(18, 30))


async def test_set_notify_time_to_none_keeps_next_watering_at_untouched(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, time(9, 0), now=NOW)
    scheduled = zone.next_watering_at

    await ws.set_notify_time(session, zone, None)

    assert zone.notify_time is None
    assert zone.next_watering_at == scheduled


@pytest.mark.parametrize("days", [0, -1, 366])
async def test_add_zone_rejects_bad_interval(session, user_id, days):
    with pytest.raises(ValueError, match="interval_days"):
        await ws.add_zone(session, user_id, "Балкон", days, now=NOW)


@pytest.mark.parametrize("name", ["", "   ", "х" * 101])
async def test_add_zone_rejects_bad_name(session, user_id, name):
    with pytest.raises(ValueError, match="name"):
        await ws.add_zone(session, user_id, name, 3, now=NOW)


async def test_add_zone_rejects_duplicate_name_ignoring_case_and_spaces(session, user_id):
    await ws.add_zone(session, user_id, "Подоконник", 7, now=NOW)

    # SQLite LOWER() не трогает кириллицу — регистр должен сравниваться в Python.
    with pytest.raises(ws.ZoneAlreadyExists):
        await ws.add_zone(session, user_id, " подоконник ", 3, now=NOW)


async def test_same_zone_name_allowed_for_different_users(session, user_id):
    other = await crud.get_or_create_user(session, telegram_id=999, username=None, full_name=None)
    await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    zone = await ws.add_zone(session, other.id, "Балкон", 7, now=NOW)

    assert zone.user_id == other.id


async def test_get_zone_is_scoped_to_owner(session, user_id):
    other = await crud.get_or_create_user(session, telegram_id=999, username=None, full_name=None)
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    assert await crud.get_zone(session, zone.id, user_id) is not None
    assert await crud.get_zone(session, zone.id, other.id) is None


# ---------- полил / отложить / интервал ----------


async def test_mark_watered_counts_next_from_now_and_resets_notification(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 5, now=NOW)
    zone.notified_at = NOW + timedelta(days=5)

    watered_at = NOW + timedelta(days=6)
    await ws.mark_watered(session, zone, now=watered_at)

    assert zone.last_watered_at == watered_at
    assert zone.next_watering_at == watered_at + timedelta(days=5)
    assert zone.notified_at is None


async def test_snooze_moves_next_watering_but_keeps_interval(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    later = NOW + timedelta(days=7)
    await ws.snooze(session, zone, 2, now=later)

    assert zone.next_watering_at == later + timedelta(days=2)
    assert zone.interval_days == 7


async def test_snooze_rejects_nonsense_days(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    with pytest.raises(ValueError, match="interval_days"):
        await ws.snooze(session, zone, 0, now=NOW)


async def test_set_interval_restarts_countdown(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 30, now=NOW)

    later = NOW + timedelta(days=10)
    await ws.set_interval(session, zone, 3, now=later)

    assert zone.interval_days == 3
    assert zone.next_watering_at == later + timedelta(days=3)


async def test_remove_deletes_zone(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    await ws.remove(session, zone)

    assert await crud.get_zone(session, zone.id, user_id) is None


async def test_list_zones_puts_most_urgent_first(session, user_id):
    await ws.add_zone(session, user_id, "Редко", 30, now=NOW)
    await ws.add_zone(session, user_id, "Часто", 2, now=NOW)
    await ws.add_zone(session, user_id, "Средне", 7, now=NOW)

    zones = await crud.list_zones(session, user_id)

    assert [z.name for z in zones] == ["Часто", "Средне", "Редко"]


async def test_rename_changes_only_the_name(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    await ws.rename(session, zone, "  Лоджия ")

    assert zone.name == "Лоджия"
    assert zone.interval_days == 7
    assert zone.next_watering_at == NOW + timedelta(days=7)


async def test_rename_allows_changing_only_case_of_own_name(session, user_id):
    zone = await ws.add_zone(session, user_id, "балкон", 7, now=NOW)

    await ws.rename(session, zone, "Балкон")

    assert zone.name == "Балкон"


async def test_rename_rejects_name_of_another_zone(session, user_id):
    await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)
    other = await ws.add_zone(session, user_id, "Подоконник", 3, now=NOW)

    with pytest.raises(ws.ZoneAlreadyExists):
        await ws.rename(session, other, " балкон ")

    assert other.name == "Подоконник"


@pytest.mark.parametrize("name", ["", "   ", "х" * 101])
async def test_rename_rejects_bad_name(session, user_id, name):
    zone = await ws.add_zone(session, user_id, "Балкон", 7, now=NOW)

    with pytest.raises(ValueError, match="name"):
        await ws.rename(session, zone, name)


async def test_list_all_zones_spans_users_most_urgent_first(session, user_id):
    other_user = await crud.get_or_create_user(session, telegram_id=987654321, username="other", full_name="Other")
    await session.commit()
    await ws.add_zone(session, user_id, "Редко", 30, now=NOW)
    await ws.add_zone(session, other_user.id, "Часто", 2, now=NOW)

    rows = await crud.list_all_zones(session)

    assert [(zone.name, user.id) for zone, user in rows] == [("Часто", other_user.id), ("Редко", user_id)]


# ---------- тексты ----------


def test_describe_due_variants():
    assert ws.describe_due(NOW + timedelta(days=3), NOW) == "через 3 дн."
    assert ws.describe_due(NOW + timedelta(days=2, hours=1), NOW) == "через 3 дн."
    assert ws.describe_due(NOW + timedelta(hours=5), NOW) == "меньше чем через сутки"
    assert ws.describe_due(NOW - timedelta(hours=5), NOW) == "пора поливать"
    assert ws.describe_due(NOW - timedelta(days=2, hours=1), NOW) == "просрочено на 2 дн."


def test_describe_last_watered_variants():
    assert ws.describe_last_watered(None, NOW) == "ещё не отмечено"
    assert ws.describe_last_watered(NOW - timedelta(hours=3), NOW) == "сегодня"
    assert ws.describe_last_watered(NOW - timedelta(days=4), NOW) == "4 дн. назад"


async def test_renders_escape_html_in_zone_name(session, user_id):
    zone = await ws.add_zone(session, user_id, "<b>Хак</b> & Ко", 7, now=NOW)

    for text in (
        ws.render_overview([zone], NOW),
        ws.render_card(zone, NOW),
        ws.render_reminder(zone),
    ):
        assert "<b>Хак</b>" not in text
        assert "&lt;b&gt;Хак&lt;/b&gt; &amp; Ко" in text


def test_render_overview_for_empty_list_invites_to_add_zone():
    assert "Пока нет ни одной зоны" in ws.render_overview([], NOW)


# ---------- выборка «пора напомнить» ----------


async def test_list_due_zones_returns_only_ripe_ones_with_owner_telegram_id(session, user_id):
    ripe = await ws.add_zone(session, user_id, "Созрела", 3, now=NOW)
    await ws.add_zone(session, user_id, "Рано", 10, now=NOW)

    due = await crud.list_due_zones(session, NOW + timedelta(days=3))

    assert [(z.id, tg) for z, tg in due] == [(ripe.id, 123456789)]


async def test_list_due_zones_skips_already_notified_zone(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 3, now=NOW)
    check_time = NOW + timedelta(days=3)
    zone.notified_at = check_time
    await session.commit()

    assert await crud.list_due_zones(session, check_time + timedelta(days=1)) == []


async def test_list_due_zones_notifies_again_after_snooze_expires(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 3, now=NOW)
    first_due = NOW + timedelta(days=3)
    zone.notified_at = first_due
    await session.commit()

    await ws.snooze(session, zone, 2, now=first_due)

    assert await crud.list_due_zones(session, first_due + timedelta(days=1)) == []
    assert len(await crud.list_due_zones(session, first_due + timedelta(days=2))) == 1


async def test_list_due_zones_after_watering_waits_for_next_cycle(session, user_id):
    zone = await ws.add_zone(session, user_id, "Балкон", 3, now=NOW)
    due_time = NOW + timedelta(days=3)
    await ws.mark_watered(session, zone, now=due_time)

    assert await crud.list_due_zones(session, due_time + timedelta(days=2)) == []
    assert len(await crud.list_due_zones(session, due_time + timedelta(days=3))) == 1
