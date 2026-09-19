"""Логика зон полива: создание, «полил», «отложить», смена интервала и
тексты для сообщений. Всё, что связано со временем, принимает now
параметром (по умолчанию — текущий момент), чтобы тесты не зависели от
реальных часов.

Время везде — наивный UTC, как в остальных таблицах проекта."""

import math
import re
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import WateringZone

MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365
MAX_NAME_LENGTH = 100

# Кнопки быстрого выбора интервала при создании зоны.
INTERVAL_PRESETS = (1, 2, 3, 5, 7, 10, 14, 21, 30)
# На сколько дней можно отложить полив из напоминания.
SNOOZE_OPTIONS = (1, 2, 3)

_INTERVAL_RE = re.compile(r"(\d{1,4})\s*(?:д\w*|d\w*)?\.?")


class ZoneAlreadyExists(Exception):
    """У пользователя уже есть зона с таким названием (без учёта регистра)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_interval(text: str) -> int | None:
    """Достаёт число дней из пользовательского ввода: «7», «10 дней»,
    «3д». Возвращает None, если это не число или оно вне допустимых
    границ — вызывающий код в этом случае просит ввести заново."""
    match = _INTERVAL_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    days = int(match.group(1))
    if not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS:
        return None
    return days


def _check_interval(days: int) -> None:
    if not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS:
        raise ValueError(f"interval_days must be in [{MIN_INTERVAL_DAYS}, {MAX_INTERVAL_DAYS}], got {days}")


async def add_zone(
    session: AsyncSession, user_id: int, name: str, interval_days: int, now: datetime | None = None
) -> WateringZone:
    """Создаёт зону; первое напоминание — через interval_days дней."""
    _check_interval(interval_days)
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be 1..{MAX_NAME_LENGTH} chars")
    if await crud.get_zone_by_name(session, user_id, name):
        raise ZoneAlreadyExists(name)

    now = now or utcnow()
    zone = await crud.create_zone(session, user_id, name, interval_days, now + timedelta(days=interval_days))
    await session.commit()
    return zone


async def mark_watered(session: AsyncSession, zone: WateringZone, now: datetime | None = None) -> None:
    """Зона полита: следующий срок отсчитывается от этого момента (а не от
    прежнего срока), потому что растениям важно время с последнего
    реального полива."""
    now = now or utcnow()
    zone.last_watered_at = now
    zone.next_watering_at = now + timedelta(days=zone.interval_days)
    zone.notified_at = None
    await session.commit()


async def snooze(session: AsyncSession, zone: WateringZone, days: int, now: datetime | None = None) -> None:
    """Отложить полив: напоминание придёт снова через days дней от сейчас.
    notified_at не трогаем — срок сдвинулся вперёд, поэтому условие в
    crud.list_due_zones само снова разрешит напоминание, когда он настанет."""
    _check_interval(days)
    now = now or utcnow()
    zone.next_watering_at = now + timedelta(days=days)
    await session.commit()


async def set_interval(session: AsyncSession, zone: WateringZone, days: int, now: datetime | None = None) -> None:
    """Меняет периодичность. Отсчёт до следующего полива стартует заново
    от текущего момента — так поведение предсказуемо: сменил интервал на 3,
    напоминание придёт через 3 дня, а не «немедленно, потому что с
    последнего полива уже прошло больше трёх»."""
    _check_interval(days)
    now = now or utcnow()
    zone.interval_days = days
    zone.next_watering_at = now + timedelta(days=days)
    zone.notified_at = None
    await session.commit()


async def remove(session: AsyncSession, zone: WateringZone) -> None:
    await crud.delete_zone(session, zone)
    await session.commit()


# ---------- Тексты ----------


def is_due(zone: WateringZone, now: datetime) -> bool:
    return zone.next_watering_at <= now


def describe_due(next_at: datetime, now: datetime) -> str:
    """Человекочитаемо: сколько осталось до полива или насколько просрочен.
    Даты умышленно не показываем — у пользователя свой часовой пояс, а мы
    считаем в UTC, и «завтра» могло бы оказаться неверным днём."""
    delta = next_at - now
    if delta <= timedelta(0):
        overdue_days = (-delta).days
        return "пора поливать" if overdue_days == 0 else f"просрочено на {overdue_days} дн."
    days = math.ceil(delta / timedelta(days=1))
    return "меньше чем через сутки" if days <= 1 else f"через {days} дн."


def describe_last_watered(last: datetime | None, now: datetime) -> str:
    if last is None:
        return "ещё не отмечено"
    days = (now - last).days
    return "сегодня" if days <= 0 else f"{days} дн. назад"


def render_overview(zones: list[WateringZone], now: datetime) -> str:
    if not zones:
        return (
            "💧 <b>Зоны полива</b>\n\n"
            "Пока нет ни одной зоны. Добавь зону — назови её и выбери, как часто поливать, "
            "а я буду напоминать."
        )
    lines = ["💧 <b>Зоны полива</b>\n"]
    for zone in zones:
        icon = "🔔" if is_due(zone, now) else "🌱"
        lines.append(
            f"{icon} <b>{escape(zone.name)}</b> — раз в {zone.interval_days} дн., {describe_due(zone.next_watering_at, now)}"
        )
    return "\n".join(lines)


def render_card(zone: WateringZone, now: datetime) -> str:
    return (
        f"💧 <b>{escape(zone.name)}</b>\n\n"
        f"Поливать: раз в {zone.interval_days} дн.\n"
        f"Следующий полив: {describe_due(zone.next_watering_at, now)}\n"
        f"Последний полив: {describe_last_watered(zone.last_watered_at, now)}"
    )


def render_reminder(zone: WateringZone) -> str:
    return f"💧 Пора полить зону «{escape(zone.name)}»"
