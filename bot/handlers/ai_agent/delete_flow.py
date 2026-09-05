"""Сценарий удаления РАСТЕНИЯ через ИИ-агента (удаление группы — см.
group_actions.handle_delete_group_intent).

Всегда идёт через подтверждение:

  1) Если совпадение по имени одно — сразу "Точно удалить «X»?" [Удалить] [Отмена]
  2) Если совпадений несколько — сперва список на выбор (по группам, если
     они лежат в разных группах, иначе по комментарию/номеру), затем то же
     подтверждение "Точно удалить «X»?" для выбранного растения.

Основное сопоставление опечаток/сокращений/перевода делает сама модель —
ей передаётся полный список названий растений пользователя, и промпт просит
вернуть plant_name точно как в этом списке (см. ai_service._PLANTS_BLOCK_TEMPLATE).
_find_matches ниже — подстраховка на случай, если модель всё равно вернула
текст пользователя как есть (например, при кратковременной деградации
качества ответа): сперва точное совпадение, затем вхождение подстроки в
обе стороны, и только в крайнем случае — приблизительное совпадение по
difflib (не помогает с разными языками/транслитерацией — там сопоставить
может только сама модель, здесь только опечатки в пределах одного письма)."""

import difflib

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group, Plant
from bot.services import plant_service

from . import router
from .common import reply
from .keyboards import confirm_delete_keyboard, delete_pick_keyboard
from .states import AIDelete

_FUZZY_CUTOFF = 0.72  # ниже — слишком много случайных совпадений на коротких названиях


def _find_matches(all_plants: list[Plant], query: str) -> list[Plant]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return []

    exact = [p for p in all_plants if p.name.strip().lower() == normalized_query]
    if exact:
        return exact

    substring = [
        p
        for p in all_plants
        if normalized_query in p.name.strip().lower() or p.name.strip().lower() in normalized_query
    ]
    if substring:
        return substring

    names_lower = [p.name.strip().lower() for p in all_plants]
    close = set(difflib.get_close_matches(normalized_query, names_lower, n=5, cutoff=_FUZZY_CUTOFF))
    if not close:
        return []
    return [p for p in all_plants if p.name.strip().lower() in close]


async def show_confirm_delete(
    reply_target: Message | CallbackQuery, state: FSMContext, plant_id: int, plant_name: str
) -> None:
    await state.set_state(AIDelete.confirm_delete)
    await state.update_data(plant_id=plant_id)
    await reply(reply_target, f"🗑 Точно удалить «{plant_name}»?", confirm_delete_keyboard().as_markup())


async def handle_delete_intent(
    message: Message,
    state: FSMContext,
    user_id: int,
    intent: dict,
    groups: list[Group] | None = None,
    ungrouped: list[Plant] | None = None,
) -> None:
    """Точка входа из entrypoint.handle_free_text для action == "delete".
    groups/ungrouped можно передать уже загруженными (entrypoint их и так
    запрашивает для списка растений в промпте) — тогда повторный запрос к
    БД не делается."""
    if groups is None or ungrouped is None:
        async with get_session() as session:
            groups, ungrouped = await crud.get_full_tree(session, user_id)

    all_plants = ungrouped[:] + [p for g in groups for p in g.plants]
    matches = _find_matches(all_plants, intent["plant_name"])

    if not matches:
        await message.answer(
            f"Не нашла растение «{intent['plant_name']}». Проверь 📋 Список или удали вручную из списка"
        )
        return

    if len(matches) == 1:
        await show_confirm_delete(message, state, matches[0].id, matches[0].name)
        return

    group_name_by_id = {g.id: g.name for g in groups}
    multi_group = len({p.group_id for p in matches}) > 1
    await state.set_state(AIDelete.pick_plant)
    await message.answer(
        f"Нашла несколько растений «{intent['plant_name']}» — какое удалить?",
        reply_markup=delete_pick_keyboard(matches, group_name_by_id, multi_group).as_markup(),
    )


@router.callback_query(StateFilter(AIDelete.pick_plant), F.data.startswith("aidelpick:"))
async def ai_delete_pick(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    plant_id = int(callback.data.split(":", 1)[1])
    await callback.answer()

    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
    if plant is None:
        await state.clear()
        await callback.message.edit_text("⚠️ Растение уже удалено, возможно, кем-то другим.")
        return

    await show_confirm_delete(callback, state, plant.id, plant.name)


@router.callback_query(StateFilter(AIDelete.confirm_delete), F.data == "aidelconfirm")
async def ai_delete_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()

    async with get_session() as session:
        plant = await crud.get_plant(session, data.get("plant_id"), user_id)
        if plant is None:
            await state.clear()
            await callback.message.edit_text("⚠️ Растение уже удалено, возможно, кем-то другим.")
            return
        name = plant.name
        await plant_service.remove_plant(session, plant)

    await state.clear()
    await callback.message.edit_text(f"🗑 Удалила «{name}»")


@router.callback_query(StateFilter(AIDelete.pick_plant, AIDelete.confirm_delete), F.data == "aidelcancel")
async def ai_delete_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
