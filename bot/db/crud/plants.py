from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Group, Plant


async def create_plant(
    session: AsyncSession,
    user_id: int,
    name: str,
    group_id: int | None = None,
    comment: str | None = None,
) -> Plant:
    plant = Plant(user_id=user_id, name=name.strip(), group_id=group_id, comment=comment)
    session.add(plant)
    await session.flush()
    return plant


async def get_plant(session: AsyncSession, plant_id: int, user_id: int) -> Plant | None:
    result = await session.execute(
        select(Plant).where(Plant.id == plant_id, Plant.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def find_plant_by_name(
    session: AsyncSession, user_id: int, name: str, group_id: int | None
) -> Plant | None:
    """Ищет растение с таким же именем (без учёта регистра) в той же
    группе (group_id=None -> среди растений без группы) — используется
    для проверки на повтор перед добавлением. Раз дубли разрешены (по
    подтверждению), совпадений может быть несколько — берём первое,
    а не scalar_one_or_none(), который упал бы с ошибкой на 2+."""
    result = await session.execute(
        select(Plant).where(
            Plant.user_id == user_id,
            Plant.group_id == group_id,
            func.lower(Plant.name) == name.strip().lower(),
        )
    )
    return result.scalars().first()


async def find_plant_by_name_any_group(
    session: AsyncSession, user_id: int, name: str
) -> Plant | None:
    """Как find_plant_by_name, но без учёта группы — ищет совпадение
    по имени среди всех растений пользователя. Используется для ранней
    проверки на повтор сразу после ввода названия, ещё до выбора группы."""
    result = await session.execute(
        select(Plant).where(
            Plant.user_id == user_id,
            func.lower(Plant.name) == name.strip().lower(),
        )
    )
    return result.scalars().first()


async def delete_plant(session: AsyncSession, plant: Plant) -> None:
    await session.delete(plant)
    await session.flush()


_UNSET = object()


async def update_plant(
    session: AsyncSession,
    plant: Plant,
    name: str | None = None,
    comment: str | None = _UNSET,  # type: ignore[assignment]
    group_id: int | None = _UNSET,  # type: ignore[assignment]
) -> None:
    """Обновляет поля растения. comment/group_id используют сентинел _UNSET,
    чтобы отличить "не менять" от "сбросить на None" (например, убрать из группы)."""
    if name is not None:
        plant.name = name.strip()
    if comment is not _UNSET:
        plant.comment = comment
    if group_id is not _UNSET:
        plant.group_id = group_id
    await session.flush()


async def get_full_tree(session: AsyncSession, user_id: int) -> tuple[list[Group], list[Plant]]:
    """Возвращает (группы с растениями, растения без группы) для общего списка."""
    groups_result = await session.execute(
        select(Group)
        .where(Group.user_id == user_id)
        .options(selectinload(Group.plants))
        .order_by(Group.name)
    )
    groups = list(groups_result.scalars())

    ungrouped_result = await session.execute(
        select(Plant).where(Plant.user_id == user_id, Plant.group_id.is_(None)).order_by(Plant.name)
    )
    ungrouped = list(ungrouped_result.scalars())

    return groups, ungrouped
