from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.services import group_service

router = Router(name="groups")


class RenameGroup(StatesGroup):
    choosing = State()
    new_name = State()


@router.message(Command("rename_group"))
async def cmd_rename_group(message: Message, state: FSMContext) -> None:
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id)
        groups = await crud.list_groups(session, user.id)

    if not groups:
        await message.answer("Групп пока нет.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"rename_pick:{group.id}")
    builder.adjust(1)

    await state.set_state(RenameGroup.choosing)
    await message.answer("Какую группу переименовать?", reply_markup=builder.as_markup())


@router.callback_query(StateFilter(RenameGroup.choosing), F.data.startswith("rename_pick:"))
async def rename_pick(callback: CallbackQuery, state: FSMContext) -> None:
    group_id = int(callback.data.split(":", 1)[1])
    await state.update_data(group_id=group_id)
    await state.set_state(RenameGroup.new_name)
    await callback.answer()
    await callback.message.edit_text("Новое название группы?")


@router.message(StateFilter(RenameGroup.new_name))
async def rename_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

    # группа гарантированно принадлежит пользователю, так как id брали
    # из его же списка групп на предыдущем шаге (cmd_rename_group)
    async with get_session() as session:
        from sqlalchemy import select as sa_select
        from bot.db.models import Group

        res = await session.execute(sa_select(Group).where(Group.id == data["group_id"]))
        group = res.scalar_one_or_none()
        if group is None:
            await state.clear()
            await message.answer("Группа не найдена, возможно уже удалена.")
            return

        old_name = group.name
        await group_service.rename(session, group, message.text.strip())

    await state.clear()
    await message.answer(f"Готово: «{old_name}» → «{message.text.strip()}»")
