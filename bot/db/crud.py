from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Group, Plant, User


async def get_or_create_user(session: AsyncSession, telegram_id: int) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.flush()
    return user


# ---------- Группы ----------

async def get_group_by_name(session: AsyncSession, user_id: int, name: str) -> Group | None:
    """Регистронезависимый поиск группы с обрезкой пробелов —
    чтобы "Суккуленты" и "суккуленты " матчились в одну группу."""
    normalized = name.strip().lower()
    result = await session.execute(select(Group).where(Group.user_id == user_id))
    for group in result.scalars():
        if group.name.strip().lower() == normalized:
            return group
    return None


async def create_group(session: AsyncSession, user_id: int, name: str) -> Group:
    group = Group(user_id=user_id, name=name.strip())
    session.add(group)
    await session.flush()
    return group


async def get_or_create_group(session: AsyncSession, user_id: int, name: str) -> tuple[Group, bool]:
    """Возвращает (группа, была_ли_создана_заново)."""
    existing = await get_group_by_name(session, user_id, name)
    if existing:
        return existing, False
    return await create_group(session, user_id, name), True


async def rename_group(session: AsyncSession, group: Group, new_name: str) -> None:
    group.name = new_name.strip()
    await session.flush()


async def delete_group(session: AsyncSession, group: Group) -> None:
    # растения не удаляются, просто становятся "без группы"
    for plant in group.plants:
        plant.group_id = None
    await session.delete(group)
    await session.flush()


async def list_groups(session: AsyncSession, user_id: int) -> list[Group]:
    result = await session.execute(
        select(Group).where(Group.user_id == user_id).order_by(Group.name)
    )
    return list(result.scalars())


# ---------- Растения ----------

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


async def delete_plant(session: AsyncSession, plant: Plant) -> None:
    await session.delete(plant)
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
