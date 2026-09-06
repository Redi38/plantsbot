"""Проверка повтора перед известной группой и финальное сохранение растения."""

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.utils.chat import pop_tracked, safe_delete_message

from .ui import ask_comment, warn_duplicate


async def proceed_after_group(
    event: Message | CallbackQuery, state: FSMContext, user_id: int, name: str, group_id: int | None
) -> None:
    """Используется только для добавления с уже заранее известной
    группой (запуск из просмотра конкретного списка) — там до выбора
    группы дела не доходит вовсе, так что группа известна сразу же
    после ввода названия, и проверять повтор можно сразу же в её
    рамках."""
    async with get_session() as session:
        existing = await crud.find_plant_by_name(session, user_id, name, group_id)

    if existing:
        await warn_duplicate(event, state, existing.name)
        return

    await ask_comment(event, state)


async def finalize_add(message: Message, state: FSMContext, user_id: int, comment: str | None) -> None:
    data = await state.get_data()
    return_token = data.get("return_token")
    tracked_id = await pop_tracked(state)
    async with get_session() as session:
        plant = await crud.create_plant(
            session, user_id, data["name"], group_id=data.get("group_id"), comment=comment
        )
        await session.commit()
        await state.clear()
        group_token = return_token if return_token else ("none" if data.get("group_id") is None else str(data["group_id"]))
        text = f"🌱 Добавила «{plant.name}»"
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 Список", callback_data=f"lg:{group_token}", style="primary")
        if tracked_id:
            await safe_delete_message(message.bot, message.chat.id, tracked_id)
        await message.answer(text, reply_markup=builder.as_markup())
