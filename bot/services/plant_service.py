from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Plant
from bot.utils.fuzzy import fuzzy_find
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
    group_id: int | None = None,
    comment: str | None = None,
    force: bool = False,
) -> Plant:
    """group_id, если передан, имеет приоритет над group_name — используется,
    когда группа уже точно известна (выбрана пользователем кнопкой или найдена
    точным совпадением), чтобы не гонять её ещё раз через get_or_create_group.

    force=True полностью отключает проверку на дубль — используется, когда
    пользователь уже подтвердил добавление повторного экземпляра раньше
    (например через "➕ Всё равно добавить"), чтобы та же проверка не
    сработала ещё раз для конкретной группы, выбранной уже ПОСЛЕ этого
    подтверждения."""
    if group_id is None and group_name:
        group, _ = await crud.get_or_create_group(session, user_id, group_name)
        group_id = group.id

    if not force:
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


async def find_plants_by_term(session: AsyncSession, user_id: int, term: str) -> list[Plant]:
    """Ищет растения не по полному имени, а по "роду" — первому слову в
    названии (например запрос "алоказии" должен найти и "Алоказия Полли",
    и "Алоказия Одора"). Используется в ИИ-агенте для action="list", когда
    group_name не совпал ни с одной реальной группой пользователя — то есть,
    скорее всего, это не группа, а вид/род растений внутри уже существующих
    групп или "без группы". Ручного аналога у этого поиска нет (вручную
    можно только открыть готовую группу), поэтому namedtuple с CRUD-кнопками
    сюда не нужен — только текст на просмотр."""
    groups, ungrouped = await crud.get_full_tree(session, user_id)
    all_plants = ungrouped[:] + [p for g in groups for p in g.plants]
    return fuzzy_find(all_plants, term, key=lambda p: p.name.split()[0] if p.name.split() else p.name)


def render_term_matches(term: str, matches: list[Plant]) -> str:
    return _render_group_block(f'Похожие на "{term}"', matches)
