"""Меню «🧪 Аптечка»: открыть список препаратов, открыть карточку, вернуться."""

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.reply import BTN_MEDS
from bot.utils.chat import begin_dialog, delete_user_message, safe_delete_message, safe_edit_text

from . import router
from .common import edit_to_card, leave_dialog, menu_view, pick_view, table_view


@router.message(F.text == BTN_MEDS)
async def cmd_meds(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        await safe_delete_message(message.bot, message.chat.id, old_msg_id)
    text, kb = await menu_view(user_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "medmenu")
async def meds_back(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await leave_dialog(state)
    text, kb = await menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^med:\d+$"))
async def medicine_open(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    medicine_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await leave_dialog(state)
    await edit_to_card(callback.message, user_id, medicine_id)


async def _show_or_back_to_menu(callback: CallbackQuery, user_id: int, view) -> None:
    """Показывает экран view; если типа/препарата уже нет (удалили с другого
    экрана) — возвращает в меню с пояснением."""
    if view is None:
        text, kb = await menu_view(user_id, "⚠️ Этот тип уже пуст или удалён.")
    else:
        text, kb = view
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.regexp(r"^mg:(all|[0-9a-f]{8})$"))
async def medicine_group_open(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    await leave_dialog(state)
    await _show_or_back_to_menu(callback, user_id, await table_view(user_id, token))


@router.callback_query(F.data.regexp(r"^medpage:(all|[0-9a-f]{8}):\d+$"))
async def medicine_group_page(callback: CallbackQuery, user_id: int) -> None:
    _, token, page = callback.data.split(":")
    await callback.answer()
    await _show_or_back_to_menu(callback, user_id, await table_view(user_id, token, int(page)))


@router.callback_query(F.data.regexp(r"^medpick:(all|[0-9a-f]{8})$"))
async def medicine_pick(callback: CallbackQuery, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    await _show_or_back_to_menu(callback, user_id, await pick_view(user_id, token))
