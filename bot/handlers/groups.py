from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db.database import get_session
from bot.services import group_service

router = Router(name="groups")


class RenameGroup(StatesGroup):
    new_name = State()


@router.callback_query(F.data.startswith("lgrename:"))
async def lgrename_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Переименование прямо из просмотра конкретной группы — группа уже
    известна из токена, поэтому сразу спрашиваем новое название."""
    token = callback.data.split(":", 1)[1]
    await state.clear()
    await state.update_data(group_id=int(token))
    await state.set_state(RenameGroup.new_name)
    await callback.answer()
    await callback.message.answer("Новое название группы?")


@router.message(StateFilter(RenameGroup.new_name))
async def rename_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

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
