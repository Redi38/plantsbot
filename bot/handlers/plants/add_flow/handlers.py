"""Зарегистрированные хендлеры диалога добавления растения."""

from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.handlers.list_view import show_group_page
from bot.keyboards.reply import BTN_ADD, MENU_BUTTONS
from bot.utils.chat import begin_dialog, render, safe_delete_message, track_callback

from .. import router
from ..common import cancel_keyboard
from .actions import finalize_add, proceed_after_group
from .ui import AddPlant, ask_comment, show_group_choice, warn_duplicate


@router.message(F.text == BTN_ADD)
async def cmd_add(message: Message, state: FSMContext) -> None:
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        await safe_delete_message(message.bot, message.chat.id, old_msg_id)
    await state.set_state(AddPlant.name)
    await render(message, state, "🌱 Как называется растение?", reply_markup=cancel_keyboard().as_markup())


@router.callback_query(F.data.startswith("lgadd:"))
async def lgadd_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавление растения прямо из просмотра конкретного списка — группа
    берётся из токена этого списка (кроме "all", где группу всё равно
    нужно выбрать), а после добавления бот возвращается на этот же список."""
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    old_msg_id = await begin_dialog(state)
    if old_msg_id and old_msg_id != callback.message.message_id:
        await safe_delete_message(callback.bot, callback.message.chat.id, old_msg_id)
    await state.update_data(return_token=token)
    if token != "all":
        await state.update_data(preset_group_id=None if token == "none" else int(token))
    await state.set_state(AddPlant.name)
    await callback.message.edit_text("🌱 Как называется растение?", reply_markup=cancel_keyboard().as_markup())
    await track_callback(callback, state)


@router.callback_query(F.data == "addcancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Отмена добавления растения на любом шаге — если начали из
    конкретного списка, возвращаемся туда, иначе просто закрываем диалог."""
    data = await state.get_data()
    return_token = data.get("return_token")
    await state.clear()
    await callback.answer("Отменено")
    if return_token:
        await show_group_page(callback, user_id, return_token, 1)
    else:
        await callback.message.delete()


@router.callback_query(F.data == "addforce")
async def add_force(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Пользователь подтвердил добавление, несмотря на найденный дубль."""
    await callback.answer()
    data = await state.get_data()

    if "group_id" in data:
        await ask_comment(callback, state)
        return

    await show_group_choice(callback, state, user_id)


@router.message(StateFilter(AddPlant.name), ~F.text.in_(MENU_BUTTONS))
async def add_name(message: Message, state: FSMContext, user_id: int) -> None:
    name = message.text.strip()
    await state.update_data(name=name)
    data = await state.get_data()

    async with get_session() as session:
        if "preset_group_id" in data:
            group_id = data["preset_group_id"]
            existing = None
        else:
            group_id = None
            existing = await crud.find_plant_by_name_any_group(session, user_id, name)

    if "preset_group_id" in data:
        await state.update_data(group_id=group_id)
        await proceed_after_group(message, state, user_id, name, group_id)
        return

    if existing:
        await warn_duplicate(message, state, existing.name)
        return

    await show_group_choice(message, state, user_id)


@router.callback_query(StateFilter(AddPlant.group), F.data.startswith("addgroup:"))
async def add_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AddPlant.new_group_name)
        await callback.message.edit_text("🆕 Название новой группы?", reply_markup=cancel_keyboard().as_markup())
        await track_callback(callback, state)
        return

    group_id = None if value == "none" else int(value)
    await state.update_data(group_id=group_id)
    await ask_comment(callback, state)


@router.message(StateFilter(AddPlant.new_group_name), ~F.text.in_(MENU_BUTTONS))
async def add_new_group_name(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        group, _ = await crud.get_or_create_group(session, user_id, message.text.strip())
        await session.commit()
        group_id = group.id

    await state.update_data(group_id=group_id)
    await ask_comment(message, state)


@router.message(Command("skip"), StateFilter(AddPlant.comment))
async def add_skip_comment(message: Message, state: FSMContext, user_id: int) -> None:
    await finalize_add(message, state, user_id, comment=None)


@router.message(StateFilter(AddPlant.comment), ~F.text.in_(MENU_BUTTONS))
async def add_comment(message: Message, state: FSMContext, user_id: int) -> None:
    await finalize_add(message, state, user_id, comment=message.text.strip())
