"""Точка входа из entrypoint.handle_free_text и все хендлеры экранов
подтверждения/выбора группы для сценария добавления растения."""

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group

from .. import router
from ..keyboards import duplicate_keyboard
from ..states import AIAdd
from .actions import perform_add
from .group_selection import match_group, show_confirm_group, show_pick_group


async def handle_add_intent(
    message: Message, state: FSMContext, user_id: int, existing_groups: list[Group], intent: dict
) -> None:
    """Точка входа из entrypoint.handle_free_text для action == "add"."""
    plant_name = intent["plant_name"]
    comment = intent.get("comment")
    matched_group = match_group(existing_groups, intent.get("group_name"))
    new_group_name = None
    if not matched_group and intent.get("group_name"):
        new_group_name = intent["group_name"].strip() or None

    async with get_session() as session:
        existing_plant = await crud.find_plant_by_name_any_group(session, user_id, plant_name)

    if existing_plant:
        await state.set_state(AIAdd.confirm_duplicate)
        await state.update_data(
            name=plant_name,
            comment=comment,
            group_id=matched_group.id if matched_group else None,
            new_group_name=new_group_name,
        )
        await message.answer(
            f"⚠️ «{existing_plant.name}» уже есть в списке. Добавить ещё один экземпляр?",
            reply_markup=duplicate_keyboard().as_markup(),
        )
        return

    await show_confirm_group(
        message,
        state,
        user_id,
        plant_name,
        comment,
        matched_group.id if matched_group else None,
        new_group_name=new_group_name,
    )


@router.callback_query(StateFilter(AIAdd.confirm_duplicate), F.data == "aiaddforce")
async def ai_add_force(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Пользователь подтвердил добавление, несмотря на найденный дубль по
    имени — идёт та же самая процедура подтверждения группы, что и обычно,
    только с force=True, чтобы финальная проверка дубля в конкретной
    группе не сработала повторно."""
    await callback.answer()
    data = await state.get_data()
    await show_confirm_group(
        callback,
        state,
        user_id,
        data["name"],
        data.get("comment"),
        data.get("group_id"),
        new_group_name=data.get("new_group_name"),
        force=True,
    )


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiconfirmadd")
async def ai_confirm_add(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    group_id = data.get("group_id")
    new_group_name = data.get("new_group_name")

    if group_id is None and new_group_name:
        # Группу явно назвал пользователь (или предложил ИИ), но её не было в
        # базе — создаём её только сейчас, по факту подтверждения, чтобы
        # нажатие "Отменить" на предыдущем шаге не оставляло пустую группу.
        async with get_session() as session:
            group, _ = await crud.get_or_create_group(session, user_id, new_group_name)
            await session.commit()
            group_id = group.id

    text, markup = await perform_add(
        user_id, data["name"], group_id, data.get("comment"), force=data.get("force", False)
    )
    await state.clear()
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiothergroup")
async def ai_other_group(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await show_pick_group(callback, state, user_id)


@router.callback_query(StateFilter(AIAdd.pick_group), F.data.startswith("aipickgrp:"))
async def ai_pick_group(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AIAdd.new_group_name)
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отменить", callback_data="aicancel", style="danger")
        await callback.message.edit_text("🆕 Название новой группы?", reply_markup=builder.as_markup())
        return

    group_id = None if value == "none" else int(value)
    data = await state.get_data()
    await show_confirm_group(
        callback, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.message(StateFilter(AIAdd.new_group_name), F.text)
async def ai_new_group_name(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        group, _ = await crud.get_or_create_group(session, user_id, message.text.strip())
        await session.commit()
        group_id = group.id

    data = await state.get_data()
    await show_confirm_group(
        message, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.callback_query(StateFilter(AIAdd.pick_group), F.data == "aibacktoconfirm")
async def ai_back_to_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    await show_confirm_group(
        callback,
        state,
        user_id,
        data["name"],
        data.get("comment"),
        data.get("group_id"),
        new_group_name=data.get("new_group_name"),
        force=data.get("force", False),
    )


@router.callback_query(
    StateFilter(AIAdd.confirm_duplicate, AIAdd.confirm_group, AIAdd.pick_group, AIAdd.new_group_name),
    F.data == "aicancel",
)
async def ai_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
