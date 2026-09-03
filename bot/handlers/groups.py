from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import get_session
from bot.handlers.list_view import send_group_page
from bot.services import group_service
from bot.utils.chat import pop_tracked, track_callback

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
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    await callback.message.edit_text("Новое название группы?", reply_markup=builder.as_markup())
    await track_callback(callback, state)


@router.message(StateFilter(RenameGroup.new_name))
async def rename_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tracked_id = await pop_tracked(state)

    async with get_session() as session:
        from sqlalchemy import select as sa_select

        from bot.db import crud
        from bot.db.models import Group

        res = await session.execute(sa_select(Group).where(Group.id == data["group_id"]))
        group = res.scalar_one_or_none()
        if group is None:
            await state.clear()
            await message.answer("Группа не найдена, возможно уже удалена.")
            return

        old_name = group.name
        new_name = message.text.strip()
        await group_service.rename(session, group, new_name)

        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)

    await state.clear()
    await send_group_page(
        message, user.id, str(group.id), edit_message_id=tracked_id, notice=f"Готово: «{old_name}» → «{new_name}»"
    )
