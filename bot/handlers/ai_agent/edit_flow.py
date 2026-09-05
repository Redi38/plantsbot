"""Сценарий изменения названия/комментария РАСТЕНИЯ через ИИ-агента —
аналог ручного ✏️ из списка (bot/handlers/plants/edit_delete.py), но без
лишних шагов: новое значение уже известно из текста пользователя, поэтому
после нахождения растения правка применяется сразу.

Поиск растения — та же логика, что и в delete_flow (см. её докстринг):
сперва точное имя из промпта (модель уже сопоставила опечатки/падежи/язык),
затем fuzzy_find как подстраховка. Если совпадений несколько — сначала
показываем список на выбор, затем применяем правку к выбранному.
"""

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group, Plant
from bot.utils.fuzzy import fuzzy_find

from . import router
from .keyboards import edit_pick_keyboard
from .states import AIEdit


def _find_matches(all_plants: list[Plant], query: str) -> list[Plant]:
    return fuzzy_find(all_plants, query)


async def _apply_edit(message_or_callback, plant_id: int, user_id: int, new_name: str | None, comment: str | None) -> None:
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        if plant is None:
            text = "⚠️ Растение уже удалено, возможно, кем-то другим."
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.edit_text(text)
            else:
                await message_or_callback.answer(text)
            return

        old_name = plant.name
        kwargs = {}
        if new_name:
            kwargs["name"] = new_name
        if comment is not None:
            kwargs["comment"] = comment or None
        await crud.update_plant(session, plant, **kwargs)
        await session.commit()
        final_name = plant.name

    if new_name and comment is not None:
        notice = f"✏️ «{old_name}» → «{final_name}», комментарий обновлён"
    elif new_name:
        notice = f"✏️ «{old_name}» → «{final_name}»"
    elif comment:
        notice = f"💬 «{final_name}»: комментарий обновлён"
    else:
        notice = f"💬 «{final_name}»: комментарий убран"

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(notice)
    else:
        await message_or_callback.answer(notice)


async def handle_edit_plant_intent(
    message: Message,
    state: FSMContext,
    user_id: int,
    intent: dict,
    groups: list[Group] | None = None,
    ungrouped: list[Plant] | None = None,
) -> None:
    """Точка входа из entrypoint.handle_free_text для action == "edit_plant"."""
    if groups is None or ungrouped is None:
        async with get_session() as session:
            groups, ungrouped = await crud.get_full_tree(session, user_id)

    all_plants = ungrouped[:] + [p for g in groups for p in g.plants]

    plant_name = (intent.get("plant_name") or "").strip()
    new_name = (intent.get("new_name") or "").strip() or None
    comment = intent.get("comment")
    if isinstance(comment, str):
        comment = comment.strip()

    if not plant_name:
        await message.answer("Не поняла, какое растение изменить 🤔 Используй ✏️ в 📋 Списке")
        return
    if not new_name and comment is None:
        await message.answer("Не поняла, что именно изменить — название или комментарий 🤔")
        return

    matches = _find_matches(all_plants, plant_name)
    if not matches:
        await message.answer(f"Не нашла растение «{plant_name}». Проверь 📋 Список")
        return

    if len(matches) == 1:
        await _apply_edit(message, matches[0].id, user_id, new_name, comment)
        return

    group_name_by_id = {g.id: g.name for g in groups}
    multi_group = len({p.group_id for p in matches}) > 1
    await state.set_state(AIEdit.pick_plant)
    await state.update_data(new_name=new_name, comment=comment)
    await message.answer(
        f"Нашла несколько растений «{plant_name}» — какое изменить?",
        reply_markup=edit_pick_keyboard(matches, group_name_by_id, multi_group).as_markup(),
    )


@router.callback_query(StateFilter(AIEdit.pick_plant), F.data.startswith("aieditpick:"))
async def ai_edit_pick(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    plant_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    await _apply_edit(callback, plant_id, user_id, data.get("new_name"), data.get("comment"))


@router.callback_query(StateFilter(AIEdit.pick_plant), F.data == "aieditcancel")
async def ai_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
