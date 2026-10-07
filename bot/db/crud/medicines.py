from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Medicine, User


async def create_medicine(
    session: AsyncSession,
    user_id: int,
    name: str,
    kind: str,
    active_substance: str | None = None,
    expires_at: date | None = None,
    comment: str | None = None,
) -> Medicine:
    medicine = Medicine(
        user_id=user_id,
        name=name.strip(),
        kind=kind.strip(),
        active_substance=active_substance.strip() if active_substance else None,
        expires_at=expires_at,
        comment=comment.strip() if comment else None,
    )
    session.add(medicine)
    await session.flush()
    return medicine


async def get_medicine(session: AsyncSession, medicine_id: int, user_id: int) -> Medicine | None:
    result = await session.execute(select(Medicine).where(Medicine.id == medicine_id, Medicine.user_id == user_id))
    return result.scalar_one_or_none()


async def count_medicines(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(select(func.count()).select_from(Medicine).where(Medicine.user_id == user_id))
    return result.scalar_one()


async def list_medicines(session: AsyncSession, user_id: int) -> list[Medicine]:
    """Препараты пользователя: сначала те, у кого срок годности раньше всех;
    препараты без срока — в конце списка."""
    result = await session.execute(
        select(Medicine)
        .where(Medicine.user_id == user_id)
        .order_by(Medicine.expires_at.is_(None), Medicine.expires_at, Medicine.name)
    )
    return list(result.scalars())


async def delete_medicine(session: AsyncSession, medicine: Medicine) -> None:
    await session.delete(medicine)
    await session.flush()


async def list_due_medicines(session: AsyncSession, today: date, days_before: int) -> list[tuple[Medicine, int]]:
    """Препараты по всем пользователям, про которые пора напомнить, вместе с
    telegram_id владельца (куда слать сообщение).

    Условие: срок указан, до него осталось не больше days_before дней (или
    он уже прошёл) и напоминание ещё не отправляли. Препарат, добавленный
    уже внутри этого окна, попадает в выборку сразу."""
    result = await session.execute(
        select(Medicine, User.telegram_id)
        .join(User, User.id == Medicine.user_id)
        .where(
            Medicine.expires_at.is_not(None),
            Medicine.expires_at <= today + timedelta(days=days_before),
            Medicine.notified_at.is_(None),
        )
        .order_by(Medicine.expires_at)
    )
    return [(row[0], row[1]) for row in result.all()]

