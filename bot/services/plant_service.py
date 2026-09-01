from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Plant


async def add_plant(
    session: AsyncSession,
    user_id: int,
    name: str,
    group_name: str | None = None,
    comment: str | None = None,
) -> Plant:
    group_id = None
    if group_name:
        group, _ = await crud.get_or_create_group(session, user_id, group_name)
        group_id = group.id

    plant = await crud.create_plant(session, user_id, name, group_id=group_id, comment=comment)
    await session.commit()
    return plant


async def remove_plant(session: AsyncSession, plant: Plant) -> None:
    await crud.delete_plant(session, plant)
    await session.commit()


async def render_tree(session: AsyncSession, user_id: int) -> str:
    """Рендерит общий список по группам без псевдографики —
    группы разделены пустой строкой, растения — простым маркером."""
    groups, ungrouped = await crud.get_full_tree(session, user_id)

    if not groups and not ungrouped:
        return "Пока нет ни одного растения. Добавь первое командой /add 🌱"

    blocks = ["🌿 <b>Все растения</b>"]

    for group in groups:
        block_lines = [f"<b>{group.name}</b>"]
        if not group.plants:
            block_lines.append("<i>(пусто)</i>")
        for plant in group.plants:
            suffix = f" — {plant.comment}" if plant.comment else ""
            block_lines.append(f"• {plant.name}{suffix}")
        blocks.append("\n".join(block_lines))

    if ungrouped:
        block_lines = ["<b>Без группы</b>"]
        for plant in ungrouped:
            suffix = f" — {plant.comment}" if plant.comment else ""
            block_lines.append(f"• {plant.name}{suffix}")
        blocks.append("\n".join(block_lines))

    return "\n\n".join(blocks)
