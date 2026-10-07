"""Удаление препарата и кнопки под напоминанием о сроке годности."""

import contextlib
from html import escape

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import confirm_delete_keyboard
from bot.services import medicine_service
from bot.utils.chat import safe_edit_text

from . import router
from .common import NOT_FOUND, menu_view


@router.callback_query(F.data.regexp(r"^meddel:\d+$"))
async def medicine_delete_ask(callback: CallbackQuery, user_id: int) -> None:
    medicine_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
    if medicine is None:
        await callback.answer("Препарат уже удалён", show_alert=True)
        return
    await callback.answer()
    kb = confirm_delete_keyboard(
        f"meddelc:{medicine_id}",
        f"med:{medicine_id}",
        confirm_label="🗑 Да, удалить",
        cancel_label="⬅️ Назад",
        cancel_style="primary",
    )
    await safe_edit_text(callback.message, f"🗑 Удалить «{escape(medicine.name)}» из аптечки?", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^meddelc:\d+$"))
async def medicine_delete_apply(callback: CallbackQuery, user_id: int) -> None:
    medicine_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
        if medicine is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        name = medicine.name
        await medicine_service.remove(session, medicine)
    await callback.answer("Удалено")
    text, kb = await menu_view(user_id, f"✅ «{escape(name)}» удалён из аптечки.")
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^medtrash:\d+$"))
async def reminder_trash(callback: CallbackQuery, user_id: int) -> None:
    """«Выбросил» под напоминанием: убираем препарат из аптечки без
    дополнительного подтверждения — кнопка и так говорит, что произойдёт."""
    medicine_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
        if medicine is None:
            await callback.answer("Препарат уже удалён", show_alert=True)
            await safe_edit_text(callback.message, NOT_FOUND)
            return
        name = medicine.name
        await medicine_service.remove(session, medicine)
    await callback.answer("Убрано")
    await safe_edit_text(callback.message, f"🗑 «{escape(name)}» убран из аптечки.")


@router.callback_query(F.data == "medok")
async def reminder_ok(callback: CallbackQuery) -> None:
    """«Понятно» под напоминанием: просто убираем сообщение, препарат остаётся."""
    await callback.answer()
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.delete()
