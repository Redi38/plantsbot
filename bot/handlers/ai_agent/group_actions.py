"""Создание, удаление и переименование ГРУПП через ИИ-агента.

Все три сценария без FSM для create/rename (значение уже известно из текста
пользователя, применяется сразу) — исключение только delete_group:

- create_group: создаёт группу сразу (или сообщает, что такая уже есть).
- rename_group: находит группу по названию (точно/фаззи) и сразу переименовывает.
- delete_group: переиспользует уже существующий ручной флоу удаления
  группы из bot/handlers/groups.py (те же callback_data lggdel*/lg:) —
  здесь только находим группу по имени и показываем то же меню "как
  поступить с растениями внутри неё", никакой отдельной логики под это
  не заведено (см. lggdel_menu / lggdelmove* / lggdelwith* в groups.py).
"""

from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.services import group_service


async def handle_create_group_intent(message: Message, user_id: int, intent: dict) -> None:
    group_name = intent["group_name"].strip()
    if not group_name:
        await message.answer("Не поняла, как назвать группу 🤔 Используй кнопки внизу экрана")
        return

    async with get_session() as session:
        existing = await crud.get_group_by_name(session, user_id, group_name)
        if existing:
            await message.answer(f"📁 Группа «{existing.name}» уже есть — ничего не меняла")
            return
        group = await crud.create_group(session, user_id, group_name)
        await session.commit()
        group_id = group.id
        created_name = group.name

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список", callback_data=f"lg:{group_id}", style="primary")
    await message.answer(f"📁 Создала группу «{created_name}»", reply_markup=builder.as_markup())


async def handle_delete_group_intent(message: Message, user_id: int, intent: dict) -> None:
    group_name = intent["group_name"].strip()
    if not group_name:
        await message.answer("Не поняла, какую группу удалить 🤔 Используй кнопки внизу экрана")
        return

    async with get_session() as session:
        group = await crud.get_group_by_name(session, user_id, group_name)
        if group is None:
            candidates = await crud.find_groups_fuzzy(session, user_id, group_name)

    if group is None:
        if len(candidates) == 1:
            group = candidates[0]
        elif len(candidates) > 1:
            names = ", ".join(f"«{g.name}»" for g in candidates)
            await message.answer(f"Нашла несколько похожих групп: {names}. Уточни название точнее.")
            return
        else:
            await message.answer(f"Не нашла группу «{group_name}». Проверь 📋 Список")
            return

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Удалить, растения переместить", callback_data=f"lggdelmove:{group.id}", style="primary")
    builder.button(text="🗑 Удалить вместе с растениями", callback_data=f"lggdelwith:{group.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{group.id}", style="primary")
    builder.adjust(1)
    await message.answer(
        f"🗑 Удалить группу «{group.name}» — как поступить с растениями внутри неё?",
        reply_markup=builder.as_markup(),
    )


async def handle_rename_group_intent(message: Message, user_id: int, intent: dict) -> None:
    """Находит группу по старому названию (group_name) и сразу переименовывает
    в new_name — без подтверждения, как и create_group (переименование
    легко откатить той же командой в обратную сторону, поэтому лишний
    шаг тут не нужен)."""
    old_name = (intent.get("group_name") or "").strip()
    new_name = (intent.get("new_name") or "").strip()

    if not old_name or not new_name:
        await message.answer("Не поняла, какую группу и как переименовать 🤔 Используй ✏️ в 📋 Списке")
        return

    async with get_session() as session:
        group = await crud.get_group_by_name(session, user_id, old_name)
        if group is None:
            candidates = await crud.find_groups_fuzzy(session, user_id, old_name)
            if len(candidates) == 1:
                group = candidates[0]
            elif len(candidates) > 1:
                names = ", ".join(f"«{g.name}»" for g in candidates)
                await message.answer(f"Нашла несколько похожих групп: {names}. Уточни название точнее.")
                return
            else:
                await message.answer(f"Не нашла группу «{old_name}». Проверь 📋 Список")
                return

        existing_with_new_name = await crud.get_group_by_name(session, user_id, new_name)
        if existing_with_new_name and existing_with_new_name.id != group.id:
            await message.answer(f"📁 Группа «{existing_with_new_name.name}» уже есть — придумай другое название")
            return

        renamed_from = group.name
        await group_service.rename(session, group, new_name)
        group_id, final_name = group.id, group.name

    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список", callback_data=f"lg:{group_id}", style="primary")
    await message.answer(f"✏️ «{renamed_from}» → «{final_name}»", reply_markup=builder.as_markup())
