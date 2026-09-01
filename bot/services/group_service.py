from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Group


async def rename(session: AsyncSession, group: Group, new_name: str) -> None:
    await crud.rename_group(session, group, new_name)
    await session.commit()


async def remove(session: AsyncSession, group: Group) -> None:
    await crud.delete_group(session, group)
    await session.commit()


async def get_all(session: AsyncSession, user_id: int) -> list[Group]:
    return await crud.list_groups(session, user_id)
