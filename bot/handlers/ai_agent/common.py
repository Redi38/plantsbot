"""Мелкие хелперы, общие для нескольких сценариев ИИ-агента."""

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Group, Plant
from bot.utils.fuzzy import fuzzy_find


async def reply(reply_target: Message | CallbackQuery, text: str, markup) -> None:
    """И обычное сообщение, и нажатие кнопки обрабатываются одинаково во
    всех сценариях ИИ-агента: если пришли из callback — редактируем
    существующее сообщение, если из свободного текста — отвечаем новым."""
    if isinstance(reply_target, CallbackQuery):
        await reply_target.message.edit_text(text, reply_markup=markup)
    else:
        await reply_target.answer(text, reply_markup=markup)


def find_plant_matches(all_plants: list[Plant], query: str) -> list[Plant]:
    """Обёртка над bot.utils.fuzzy.fuzzy_find — общая подстраховка для
    delete_flow и edit_flow на случай, если модель всё же вернула
    plant_name как есть, не сопоставив его со списком (см. докстринг
    delete_flow с подробностями)."""
    return fuzzy_find(all_plants, query)


def pick_context(groups: list[Group], matches: list[Plant]) -> tuple[dict[int, str], bool]:
    """group_name_by_id + multi_group — то, что нужно клавиатуре выбора
    растения (см. keyboards.plant_pick_keyboard), чтобы подписать кнопки
    группой, если совпадения лежат в разных группах."""
    group_name_by_id = {g.id: g.name for g in groups}
    multi_group = len({p.group_id for p in matches}) > 1
    return group_name_by_id, multi_group


async def resolve_group(session: AsyncSession, user_id: int, name: str) -> tuple[Group | None, list[Group]]:
    """Находит группу пользователя по названию, которое вернула модель:
    сперва точное совпадение (get_group_by_name), затем нечёткое
    (find_groups_fuzzy) как подстраховка — та же логика раньше была
    продублирована в group_actions (delete_group/rename_group) и
    entrypoint (action="list").

    Возвращает (группа, кандидаты):
      - (Group, [])       — нашли однозначно (точно или единственный fuzzy-кандидат)
      - (None, [])        — не нашли вообще ничего
      - (None, [g1, g2])  — нашлось несколько похожих, нужно уточнение у пользователя
    """
    group = await crud.get_group_by_name(session, user_id, name)
    if group:
        return group, []

    candidates = await crud.find_groups_fuzzy(session, user_id, name)
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates
