from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.handlers.list_view import send_group_page, show_group_page
from bot.keyboards.inline import groups_keyboard
from bot.keyboards.reply import BTN_ADD
from bot.services import plant_service
from bot.utils.chat import pop_tracked, render, track_callback

router = Router(name="plants")


class AddPlant(StatesGroup):
    name = State()
    group = State()
    new_group_name = State()
    comment = State()


class EditPlant(StatesGroup):
    value = State()


async def _plants_for_token(session, user_id: int, token: str) -> list:
    """Список растений, отображаемых в конкретном списке (группа / без
    группы / всё) — используется для меню выбора растения в изменении и
    удалении, вызванных из этого списка."""
    if token == "all":
        groups, ungrouped = await crud.get_full_tree(session, user_id)
        plants = list(ungrouped)
        for g in groups:
            plants.extend(g.plants)
        return plants
    if token == "none":
        _, ungrouped = await crud.get_full_tree(session, user_id)
        return list(ungrouped)
    group = await crud.get_group(session, int(token), user_id)
    return list(group.plants) if group else []


# ---------- Добавление ----------

def _cancel_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="addcancel", style="danger")
    return builder


@router.message(F.text == BTN_ADD)
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddPlant.name)
    await render(message, state, "Как называется растение?", reply_markup=_cancel_keyboard().as_markup())


@router.callback_query(F.data.startswith("lgadd:"))
async def lgadd_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавление растения прямо из просмотра конкретного списка — группа
    берётся из токена этого списка (кроме "all", где группу всё равно
    нужно выбрать), а после добавления бот возвращается на этот же список."""
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    await state.clear()
    await state.update_data(return_token=token)
    if token != "all":
        await state.update_data(preset_group_id=None if token == "none" else int(token))
    await state.set_state(AddPlant.name)
    await callback.message.edit_text("Как называется растение?", reply_markup=_cancel_keyboard().as_markup())
    await track_callback(callback, state)


@router.callback_query(F.data == "addcancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена добавления растения на любом шаге — если начали из
    конкретного списка, возвращаемся туда, иначе просто закрываем диалог."""
    data = await state.get_data()
    return_token = data.get("return_token")
    await state.clear()
    await callback.answer("Отменено")
    if return_token:
        await show_group_page(callback, return_token, 1)
    else:
        await callback.message.edit_text("Отменено")


@router.message(StateFilter(AddPlant.name))
async def add_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    data = await state.get_data()

    if "preset_group_id" in data:
        await state.update_data(group_id=data["preset_group_id"])
        await state.set_state(AddPlant.comment)
        await render(
            message, state, "Комментарий есть? Напиши текстом или пришли /skip",
            reply_markup=_cancel_keyboard().as_markup(),
        )
        return

    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        groups = await crud.list_groups(session, user.id)
        ungrouped_label = await plant_service.get_ungrouped_label(session, user.id)

    await state.set_state(AddPlant.group)
    if groups:
        await render(
            message,
            state,
            "Выбери группу:",
            reply_markup=groups_keyboard(groups, prefix="addgroup", none_label=ungrouped_label, cancel_data="addcancel"),
        )
    else:
        await state.update_data(group_id=None)
        await state.set_state(AddPlant.comment)
        await render(
            message, state, f"Групп пока нет — добавлю в «{ungrouped_label}». Комментарий есть? (или /skip)",
            reply_markup=_cancel_keyboard().as_markup(),
        )


@router.callback_query(StateFilter(AddPlant.group), F.data.startswith("addgroup:"))
async def add_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AddPlant.new_group_name)
        await callback.message.edit_text("Название новой группы?", reply_markup=_cancel_keyboard().as_markup())
        await track_callback(callback, state)
        return

    if value == "none":
        await state.update_data(group_id=None)
    else:
        await state.update_data(group_id=int(value))

    await state.set_state(AddPlant.comment)
    await callback.message.edit_text(
        "Комментарий есть? Напиши текстом или пришли /skip", reply_markup=_cancel_keyboard().as_markup()
    )
    await track_callback(callback, state)


@router.message(StateFilter(AddPlant.new_group_name))
async def add_new_group_name(message: Message, state: FSMContext) -> None:
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        group, _ = await crud.get_or_create_group(session, user.id, message.text.strip())
        await session.commit()

    await state.update_data(group_id=group.id)
    await state.set_state(AddPlant.comment)
    await render(
        message, state, "Комментарий есть? Напиши текстом или пришли /skip",
        reply_markup=_cancel_keyboard().as_markup(),
    )


@router.message(Command("skip"), StateFilter(AddPlant.comment))
async def add_skip_comment(message: Message, state: FSMContext) -> None:
    await _finalize_add(message, state, comment=None)


@router.message(StateFilter(AddPlant.comment))
async def add_comment(message: Message, state: FSMContext) -> None:
    await _finalize_add(message, state, comment=message.text.strip())


async def _finalize_add(message: Message, state: FSMContext, comment: str | None) -> None:
    data = await state.get_data()
    return_token = data.get("return_token")
    tracked_id = await pop_tracked(state)
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)

        existing = await crud.find_plant_by_name(session, user.id, data["name"], data.get("group_id"))
        if existing:
            await state.clear()
            text = f"⚠️ «{existing.name}» уже есть в этом списке — не добавляю повторно"
            group_token = return_token if return_token else ("none" if data.get("group_id") is None else str(data["group_id"]))
            builder = InlineKeyboardBuilder()
            builder.button(text="📋 Список", callback_data=f"lg:{group_token}", style="primary")
            if tracked_id:
                try:
                    await message.bot.delete_message(chat_id=message.chat.id, message_id=tracked_id)
                except TelegramBadRequest:
                    pass
            await message.answer(text, reply_markup=builder.as_markup())
            return

        plant = await crud.create_plant(
            session, user.id, data["name"], group_id=data.get("group_id"), comment=comment
        )
        await session.commit()
        await state.clear()
        group_token = return_token if return_token else ("none" if data.get("group_id") is None else str(data["group_id"]))
        text = f"🌱 Добавила «{plant.name}»"
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 Список", callback_data=f"lg:{group_token}", style="primary")
        if tracked_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=tracked_id)
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=builder.as_markup())


# ---------- Удаление ----------

@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.edit_text("Отменено")


# ---------- Удаление растения из конкретного списка ----------

@router.callback_query(F.data.startswith("lgdel:"))
async def lgdel_pick(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]
    async with get_session() as session:
        user = await crud.get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        await session.commit()
        plants = await _plants_for_token(session, user.id, token)

    await callback.answer()
    if not plants:
        await callback.answer("В этом списке пока нет растений", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plant in plants:
        builder.button(text=plant.name, callback_data=f"lgpdel:{token}:{plant.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("Какое растение удалить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpdel:"))
async def lgpdel_confirm_ask(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"lgpdelc:{token}:{plant_id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("Точно удалить это растение?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpdelc:"))
async def lgpdel_confirm(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    async with get_session() as session:
        user = await crud.get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        plant = await crud.get_plant(session, int(plant_id), user.id)
        if plant is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        await plant_service.remove_plant(session, plant)

    await callback.answer("Удалено")
    await show_group_page(callback, token, 1)


# ---------- Изменение растения из конкретного списка ----------

@router.callback_query(F.data.startswith("lgedit:"))
async def lgedit_pick(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]
    async with get_session() as session:
        user = await crud.get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        await session.commit()
        plants = await _plants_for_token(session, user.id, token)

    await callback.answer()
    if not plants:
        await callback.answer("В этом списке пока нет растений", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plant in plants:
        builder.button(text=plant.name, callback_data=f"lgpedit:{token}:{plant.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("Какое растение изменить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpedit:"))
async def lgpedit_field(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="Название", callback_data=f"lgpeditf:{token}:{plant_id}:name", style="success")
    builder.button(text="Комментарий", callback_data=f"lgpeditf:{token}:{plant_id}:comment", style="primary")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(2, 1)
    await callback.message.edit_text("Что изменить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpeditf:"))
async def lgpeditf_ask_value(callback: CallbackQuery, state: FSMContext) -> None:
    _, token, plant_id, field = callback.data.split(":", 3)
    await callback.answer()
    await state.clear()
    await state.update_data(token=token, plant_id=int(plant_id), field=field)
    await state.set_state(EditPlant.value)
    prompt = "Новое название:" if field == "name" else "Новый комментарий (или /skip, чтобы убрать):"
    await callback.message.edit_text(prompt)
    await track_callback(callback, state)


@router.message(Command("skip"), StateFilter(EditPlant.value))
async def edit_skip_value(message: Message, state: FSMContext) -> None:
    await _finalize_edit(message, state, value=None)


@router.message(StateFilter(EditPlant.value))
async def edit_value(message: Message, state: FSMContext) -> None:
    await _finalize_edit(message, state, value=message.text.strip())


async def _finalize_edit(message: Message, state: FSMContext, value: str | None) -> None:
    data = await state.get_data()
    field = data["field"]
    tracked_id = await pop_tracked(state)
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        plant = await crud.get_plant(session, data["plant_id"], user.id)
        if plant is None:
            await state.clear()
            await render(message, state, "Растение уже удалено.")
            return

        if field == "name" and value:
            await crud.update_plant(session, plant, name=value)
        elif field == "comment":
            await crud.update_plant(session, plant, comment=value)
        await session.commit()

    token = data["token"]
    await state.clear()
    await send_group_page(message, user.id, token, edit_message_id=tracked_id, notice="✏️ Изменено")
