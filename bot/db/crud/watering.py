from datetime import datetime, time

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User, WateringZone


async def create_zone(
    session: AsyncSession,
    user_id: int,
    name: str,
    interval_days: int,
    next_watering_at: datetime,
    notify_time: time | None = None,
) -> WateringZone:
    zone = WateringZone(
        user_id=user_id,
        name=name.strip(),
        interval_days=interval_days,
        next_watering_at=next_watering_at,
        notify_time=notify_time,
    )
    session.add(zone)
    await session.flush()
    return zone


async def get_zone(session: AsyncSession, zone_id: int, user_id: int) -> WateringZone | None:
    result = await session.execute(
        select(WateringZone).where(WateringZone.id == zone_id, WateringZone.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_zone_by_name(session: AsyncSession, user_id: int, name: str) -> WateringZone | None:
    """Регистронезависимый поиск зоны по имени. Как и в get_group_by_name,
    сравнение делается в Python, а не через LOWER() в SQL: SQLite без ICU
    приводит к нижнему регистру только ASCII, кириллица остаётся как есть."""
    normalized = name.strip().lower()
    result = await session.execute(select(WateringZone).where(WateringZone.user_id == user_id))
    for zone in result.scalars():
        if zone.name.strip().lower() == normalized:
            return zone
    return None


async def list_zones(session: AsyncSession, user_id: int) -> list[WateringZone]:
    """Зоны пользователя: сначала те, которым полив нужен раньше всех."""
    result = await session.execute(
        select(WateringZone)
        .where(WateringZone.user_id == user_id)
        .order_by(WateringZone.next_watering_at, WateringZone.name)
    )
    return list(result.scalars())


async def delete_zone(session: AsyncSession, zone: WateringZone) -> None:
    await session.delete(zone)
    await session.flush()


async def list_due_zones(session: AsyncSession, now: datetime) -> list[tuple[WateringZone, int]]:
    """Зоны по всем пользователям, про которые пора напомнить, вместе с
    telegram_id владельца (куда слать сообщение).

    Условие: срок наступил и по этому циклу напоминание ещё не отправляли
    (notified_at пуст либо старше срока — последнее случается после
    «отложить»: срок сдвинулся вперёд, а notified_at остался от прошлого
    напоминания)."""
    result = await session.execute(
        select(WateringZone, User.telegram_id)
        .join(User, User.id == WateringZone.user_id)
        .where(
            WateringZone.next_watering_at <= now,
            or_(WateringZone.notified_at.is_(None), WateringZone.notified_at < WateringZone.next_watering_at),
        )
        .order_by(WateringZone.next_watering_at)
    )
    return [(row[0], row[1]) for row in result.all()]


async def list_all_zones(session: AsyncSession) -> list[tuple[WateringZone, User]]:
    """Все зоны всех пользователей вместе с владельцами — для страницы
    админки «Полив». Порядок как в list_zones: сначала те, кому полив
    нужен раньше всех."""
    result = await session.execute(
        select(WateringZone, User)
        .join(User, User.id == WateringZone.user_id)
        .order_by(WateringZone.next_watering_at, WateringZone.name)
    )
    return [(row[0], row[1]) for row in result.all()]
