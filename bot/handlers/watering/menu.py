"""Меню «💧 Полив»: открыть список зон, открыть карточку конкретной
зоны, вернуться из карточки/диалога в меню."""

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.reply import BTN_WATER
from bot.utils.chat import begin_dialog, delete_user_message, safe_delete_message, safe_edit_text

from . import router
from .common import edit_to_card, leave_zone_dialog, menu_view


@router.message(F.text == BTN_WATER)
async def cmd_water(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        await safe_delete_message(message.bot, message.chat.id, old_msg_id)
    text, kb = await menu_view(user_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "wzmenu")
async def zones_back(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await leave_zone_dialog(state)
    text, kb = await menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.startswith("wz:"))
async def zone_open(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await leave_zone_dialog(state)
    await edit_to_card(callback.message, user_id, zone_id)
