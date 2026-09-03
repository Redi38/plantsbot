from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Plant
from bot.utils.text import split_long_text


class DuplicatePlantError(Exception):
    """Растение с таким именем уже есть в этой же группе."""

    def __init__(self, existing: Plant):
        self.existing = existing
        super().__init__(f"Растение «{existing.name}» уже есть в этом списке")


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

    existing = await crud.find_plant_by_name(session, user_id, name, group_id)
    if existing:
        raise DuplicatePlantError(existing)

    plant = await crud.create_plant(session, user_id, name, group_id=group_id, comment=comment)
    await session.commit()
    return plant


async def remove_plant(session: AsyncSession, plant: Plant) -> None:
    await crud.delete_plant(session, plant)
    await session.commit()


_DEFAULT_UNGROUPED_LABEL = "Без группы"


async def get_ungrouped_label(session: AsyncSession, user_id: int) -> str:
    """Кастомная подпись пользователя для растений без группы (задаётся
    только через админку), либо дефолт — она не обязательна для новых
    юзеров и ничего заранее не создаёт."""
    user = await crud.get_user(session, user_id)
    return (user.ungrouped_label if user and user.ungrouped_label else None) or _DEFAULT_UNGROUPED_LABEL


def _render_group_block(name: str, plants: list[Plant]) -> str:
    lines = [f"<b>{name} ({len(plants)})</b>"]
    if not plants:
        lines.append("<i>(пусто)</i>")
    for plant in plants:
        suffix = f" — {plant.comment}" if plant.comment else ""
        lines.append(f"• {plant.name}{suffix}")
    return "\n".join(lines)


async def render_group_pages(
    session: AsyncSession, user_id: int, group_id: int | None
) -> tuple[str, list[str]] | None:
    """Страницы для ОДНОЙ конкретной группы (group_id=None -> растения без
    группы). Возвращает (название, страницы) или None, если группа не
    найдена / принадлежит другому пользователю."""
    if group_id is None:
        _, ungrouped = await crud.get_full_tree(session, user_id)
        name, plants = await get_ungrouped_label(session, user_id), ungrouped
    else:
        group = await crud.get_group(session, group_id, user_id)
        if group is None:
            return None
        name, plants = group.name, group.plants

    block_text = _render_group_block(name, plants)
    return name, split_long_text(block_text)


async def render_pages(session: AsyncSession, user_id: int) -> list[str]:
    """Строит список страниц для /list — каждая группа отдельной страницей
    (без псевдографики: растения — простым маркером). Если текст одной
    группы всё равно не помещается в лимит Telegram, она дробится на
    несколько страниц через split_long_text."""
    groups, ungrouped = await crud.get_full_tree(session, user_id)

    if not groups and not ungrouped:
        return ["Пока нет ни одного растения. Добавь первое кнопкой ➕ Добавить 🌱"]

    blocks: list[tuple[str, list[Plant]]] = [(group.name, group.plants) for group in groups]
    if ungrouped:
        blocks.append((await get_ungrouped_label(session, user_id), ungrouped))

    total = sum(len(plants) for _, plants in blocks)
    list_header = f"🌿 <b>Все растения ({total})</b>"

    pages: list[str] = []
    for name, plants in blocks:
        block_text = _render_group_block(name, plants)
        full_text = f"{list_header}\n\n{block_text}"

        chunks = split_long_text(block_text)
        if len(chunks) == 1:
            pages.append(full_text)
        else:
            for i, chunk in enumerate(chunks):
                pages.append(f"{list_header}\n\n{chunk}" if i == 0 else chunk)

    return pages
