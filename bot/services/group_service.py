from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Group


async def rename(session: AsyncSession, group: Group, new_name: str) -> None:
    await crud.rename_group(session, group, new_name)
    await session.commit()


async def remove(session: AsyncSession, group: Group) -> None:
    """Удаляет группу, растения переводятся в "без группы"."""
    await crud.delete_group(session, group)
    await session.commit()


async def remove_with_plants(session: AsyncSession, group: Group) -> None:
    """Удаляет группу вместе со всеми растениями внутри неё."""
    await crud.delete_group_with_plants(session, group)
    await session.commit()


async def remove_move_plants(session: AsyncSession, group: Group, target_group_id: int) -> None:
    """Удаляет группу, предварительно перенося все растения в другую
    конкретную группу (target_group_id уже существующей группы)."""
    await crud.move_group_plants(session, group, target_group_id)
    await session.delete(group)
    await session.flush()
    await session.commit()


async def get_all(session: AsyncSession, user_id: int) -> list[Group]:
    return await crud.list_groups(session, user_id)
