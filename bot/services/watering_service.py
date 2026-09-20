"""Логика зон полива: создание, «полил», «отложить», смена интервала и
тексты для сообщений. Всё, что связано со временем, принимает now
параметром (по умолчанию — текущий момент), чтобы тесты не зависели от
реальных часов.

Время везде — наивный UTC, как в остальных таблицах проекта."""

import re
from datetime import datetime, time, timedelta, timezone
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import WateringZone

MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365
MAX_NAME_LENGTH = 100

# Кнопки быстрого выбора интервала при создании зоны.
INTERVAL_PRESETS = (1, 2, 3, 5, 7, 10, 14, 21, 30)
# Кнопки быстрого выбора времени напоминания (UTC) при создании/смене.
NOTIFY_TIME_PRESETS = (time(7, 0), time(9, 0), time(12, 0), time(18, 0), time(20, 0), time(21, 0))
# Смещение локального времени показа пользователю (Минск, UTC+3) от того,
# как notify_time хранится в БД (наивный UTC).
DISPLAY_UTC_OFFSET = timedelta(hours=3)
DISPLAY_TZ_LABEL = "Минск, UTC+3"
# На сколько дней можно отложить полив из напоминания.
SNOOZE_OPTIONS = (1, 2, 3)

_INTERVAL_RE = re.compile(r"(\d{1,4})\s*(?:д\w*|d\w*)?\.?")
_TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.\s]([0-5]\d)")


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


def parse_notify_time(text: str) -> time | None:
    """Достаёт час:минуту из пользовательского ввода: «9:00», «09.30»,
    «21 15». Возвращает None, если это не похоже на время — вызывающий код
    в этом случае просит ввести заново."""
    match = _TIME_RE.fullmatch(text.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def _schedule(now: datetime, days: int, notify_time: time | None) -> datetime:
    """Считает next_watering_at через days дней от now.

    Если у зоны задано фиксированное время напоминания (notify_time),
    результат всегда стоит на этот час — так уведомление приходит в одно и
    то же время суток, а не «через сколько-то дней от текущей секунды».
    Без notify_time сохраняется исходное поведение: время суток берётся
    от now (когда создали зону/полили её в последний раз)."""
    target = now + timedelta(days=days)
    if notify_time is not None:
        target = datetime.combine(target.date(), notify_time)
    return target


async def add_zone(
    session: AsyncSession,
    user_id: int,
    name: str,
    interval_days: int,
    notify_time: time | None = None,
    now: datetime | None = None,
) -> WateringZone:
    """Создаёт зону; первое напоминание — через interval_days дней (в час
    notify_time, если он задан)."""
    _check_interval(interval_days)
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be 1..{MAX_NAME_LENGTH} chars")
    if await crud.get_zone_by_name(session, user_id, name):
        raise ZoneAlreadyExists(name)

    now = now or utcnow()
    zone = await crud.create_zone(
        session, user_id, name, interval_days, _schedule(now, interval_days, notify_time), notify_time
    )
    await session.commit()
    return zone


async def mark_watered(session: AsyncSession, zone: WateringZone, now: datetime | None = None) -> None:
    """Зона полита: следующий срок отсчитывается от этого момента (а не от
    прежнего срока), потому что растениям важно время с последнего
    реального полива."""
    now = now or utcnow()
    zone.last_watered_at = now
    zone.next_watering_at = _schedule(now, zone.interval_days, zone.notify_time)
    zone.notified_at = None
    await session.commit()


async def snooze(session: AsyncSession, zone: WateringZone, days: int, now: datetime | None = None) -> None:
    """Отложить полив: напоминание придёт снова через days дней от сейчас.
    notified_at не трогаем — срок сдвинулся вперёд, поэтому условие в
    crud.list_due_zones само снова разрешит напоминание, когда он настанет."""
    _check_interval(days)
    now = now or utcnow()
    zone.next_watering_at = _schedule(now, days, zone.notify_time)
    await session.commit()


async def set_interval(session: AsyncSession, zone: WateringZone, days: int, now: datetime | None = None) -> None:
    """Меняет периодичность. Отсчёт до следующего полива стартует заново
    от текущего момента — так поведение предсказуемо: сменил интервал на 3,
    напоминание придёт через 3 дня, а не «немедленно, потому что с
    последнего полива уже прошло больше трёх»."""
    _check_interval(days)
    now = now or utcnow()
    zone.interval_days = days
    zone.next_watering_at = _schedule(now, days, zone.notify_time)
    zone.notified_at = None
    await session.commit()


async def set_notify_time(session: AsyncSession, zone: WateringZone, notify_time: time | None) -> None:
    """Меняет время напоминания. День следующего полива не трогаем —
    меняется только час, как и при переименовании зоны (rename): смена
    времени не должна сама по себе сдвигать срок на другой день.

    Если notify_time снят (None), next_watering_at остаётся как есть —
    зона возвращается к «плавающему» времени суток начиная со следующего
    пересчёта (mark_watered/snooze/set_interval)."""
    zone.notify_time = notify_time
    if notify_time is not None:
        zone.next_watering_at = datetime.combine(zone.next_watering_at.date(), notify_time)
    await session.commit()


async def rename(session: AsyncSession, zone: WateringZone, name: str) -> None:
    """Переименовывает зону. Уникальность имени проверяется так же, как при
    создании (без учёта регистра и пробелов), но саму зону из проверки
    исключаем — иначе смена «балкон» на «Балкон» считалась бы дублем.
    Расписание не трогаем: меняется только название."""
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be 1..{MAX_NAME_LENGTH} chars")
    existing = await crud.get_zone_by_name(session, zone.user_id, name)
    if existing is not None and existing.id != zone.id:
        raise ZoneAlreadyExists(name)
    zone.name = name
    await session.commit()


async def remove(session: AsyncSession, zone: WateringZone) -> None:
    await crud.delete_zone(session, zone)
    await session.commit()


# ---------- Тексты ----------


def is_due(zone: WateringZone, now: datetime) -> bool:
    return zone.next_watering_at <= now


def describe_due(next_at: datetime, now: datetime) -> str:
    """Человекочитаемо: сколько осталось до полива или насколько просрочен.

    Остаток до полива округляется вверх до целых суток: 3 суток ровно или
    2 суток и час — в обоих случаях «через 3 дн.», ведь до полива остаётся
    больше двух полных суток. Меньше одних суток — отдельная формулировка
    «меньше чем через сутки», без обманчивого «через 1 дн.». Даты
    умышленно не показываем, только число дней."""
    delta = next_at - now
    if delta <= timedelta(0):
        overdue_days = (-delta).days
        return "пора поливать" if overdue_days == 0 else f"просрочено на {overdue_days} дн."
    if delta < timedelta(days=1):
        return "меньше чем через сутки"
    days = -(-delta // timedelta(days=1))  # целочисленное округление вверх
    return "завтра" if days == 1 else f"через {days} дн."


def describe_last_watered(last: datetime | None, now: datetime) -> str:
    if last is None:
        return "ещё не отмечено"
    days = (now - last).days
    return "сегодня" if days <= 0 else f"{days} дн. назад"


def to_display_time(notify_time: time) -> time:
    """Переводит время, хранимое в БД как наивный UTC, в локальное время
    показа пользователю (Минск, UTC+3)."""
    return (datetime.combine(datetime.min, notify_time) + DISPLAY_UTC_OFFSET).time()


def from_display_time(local_time: time) -> time:
    """Обратное к to_display_time: локальное время пользователя (Минск,
    UTC+3) -> наивный UTC для хранения в БД. Базовая дата — не
    datetime.min: вычитание смещения из 00:00 первого дня выходит за
    границы datetime и падает с OverflowError."""
    return (datetime(2000, 1, 1, local_time.hour, local_time.minute) - DISPLAY_UTC_OFFSET).time()


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
        time_suffix = f", в {to_display_time(zone.notify_time).strftime('%H:%M')}" if zone.notify_time else ""
        lines.append(
            f"{icon} <b>{escape(zone.name)}</b> — раз в {zone.interval_days} дн.{time_suffix}, "
            f"{describe_due(zone.next_watering_at, now)}"
        )
    return "\n".join(lines)


def describe_notify_time(notify_time: time | None) -> str:
    if notify_time is None:
        return "без фиксированного часа"
    local = to_display_time(notify_time).strftime("%H:%M")
    return f"в <b>{local}</b> ({DISPLAY_TZ_LABEL})"


def render_card(zone: WateringZone, now: datetime) -> str:
    return (
        f"💧 <b>{escape(zone.name)}</b>\n\n"
        f"Поливать: раз в {zone.interval_days} дн.\n"
        f"Напоминание: {describe_notify_time(zone.notify_time)}\n"
        f"Следующий полив: {describe_due(zone.next_watering_at, now)}\n"
        f"Последний полив: {describe_last_watered(zone.last_watered_at, now)}"
    )


def render_reminder(zone: WateringZone) -> str:
    return f"💧 Пора полить зону «{escape(zone.name)}»"
