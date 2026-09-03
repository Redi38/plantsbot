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
from bot.keyboards.reply import BTN_ADD, MENU_BUTTONS
from bot.services import plant_service
from bot.utils.chat import begin_dialog, pop_tracked, render, track_callback

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


async def _show_step(event: Message | CallbackQuery, state: FSMContext, text: str, reply_markup) -> None:
    """Показывает следующий шаг диалога добавления — редактирует
    сообщение, если вызвано нажатием инлайн-кнопки, или использует
    render(), если вызвано сообщением пользователя (чтобы не плодить
    лишние сообщения в чате)."""
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=reply_markup)
        await track_callback(event, state)
    else:
        await render(event, state, text, reply_markup=reply_markup)


async def _ask_comment(event: Message | CallbackQuery, state: FSMContext, *, prefix: str = "") -> None:
    await state.set_state(AddPlant.comment)
    await _show_step(
        event, state, f"{prefix}💬 Комментарий есть? Напиши текстом или пришли /skip", _cancel_keyboard().as_markup()
    )


async def _warn_duplicate(event: Message | CallbackQuery, state: FSMContext, existing_name: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Всё равно добавить", callback_data="addforce", style="primary")
    builder.button(text="❌ Отмена", callback_data="addcancel", style="danger")
    builder.adjust(1)
    await _show_step(
        event, state, f"⚠️ «{existing_name}» уже есть в списке. Добавить ещё один экземпляр?", builder.as_markup()
    )


async def _show_group_choice(event: Message | CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показывает выбор группы либо, если групп ещё нет, сразу
    определяет растение в "без группы" и переходит к комментарию.
    Повтор по имени уже проверен раньше (сразу после ввода названия —
    см. add_name/add_force), поэтому здесь его заново не ищем."""
    async with get_session() as session:
        groups = await crud.list_groups(session, user_id)
        ungrouped_label = await plant_service.get_ungrouped_label(session, user_id)

    if groups:
        await state.set_state(AddPlant.group)
        await _show_step(
            event,
            state,
            "📁 Выбери группу:",
            groups_keyboard(groups, prefix="addgroup", none_label=ungrouped_label, cancel_data="addcancel"),
        )
        return

    await state.update_data(group_id=None)
    await _ask_comment(event, state, prefix=f"📁 Групп пока нет — добавлю в «{ungrouped_label}». ")


async def _proceed_after_group(
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
        await _warn_duplicate(event, state, existing.name)
        return

    await _ask_comment(event, state)


@router.message(F.text == BTN_ADD)
async def cmd_add(message: Message, state: FSMContext) -> None:
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        # Предыдущая подсказка о добавлении (из этого же или другого
        # входа — повторное нажатие "Добавить", либо переход из списка)
        # больше не актуальна — убираем её, чтобы не копилась в чате.
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=old_msg_id)
        except TelegramBadRequest:
            pass
    await state.set_state(AddPlant.name)
    await render(message, state, "🌱 Как называется растение?", reply_markup=_cancel_keyboard().as_markup())


@router.callback_query(F.data.startswith("lgadd:"))
async def lgadd_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавление растения прямо из просмотра конкретного списка — группа
    берётся из токена этого списка (кроме "all", где группу всё равно
    нужно выбрать), а после добавления бот возвращается на этот же список."""
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    old_msg_id = await begin_dialog(state)
    if old_msg_id and old_msg_id != callback.message.message_id:
        # Сценарий добавления уже был начат откуда-то ещё (повторное
        # нажатие "Добавить" из reply-клавы и т.п.) — убираем старую
        # подсказку, чтобы не копилась в чате.
        try:
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=old_msg_id)
        except TelegramBadRequest:
            pass
    await state.update_data(return_token=token)
    if token != "all":
        await state.update_data(preset_group_id=None if token == "none" else int(token))
    await state.set_state(AddPlant.name)
    await callback.message.edit_text("🌱 Как называется растение?", reply_markup=_cancel_keyboard().as_markup())
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
        # Группа была известна ещё до проверки (добавление из
        # конкретного списка) — сразу переходим к комментарию.
        await _ask_comment(callback, state)
        return

    # Дубль найден сразу по названию (reply-кнопка "Добавить"), группа
    # ещё не выбрана — продолжаем обычный шаг выбора группы.
    await _show_group_choice(callback, state, user_id)


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
            # Проверяем повтор сразу по названию (среди всех групп),
            # ещё до того, как спросить группу — это самый ранний
            # момент, когда проверка вообще имеет смысл в этом сценарии.
            existing = await crud.find_plant_by_name_any_group(session, user_id, name)

    if "preset_group_id" in data:
        await state.update_data(group_id=group_id)
        await _proceed_after_group(message, state, user_id, name, group_id)
        return

    if existing:
        await _warn_duplicate(message, state, existing.name)
        return

    await _show_group_choice(message, state, user_id)


@router.callback_query(StateFilter(AddPlant.group), F.data.startswith("addgroup:"))
async def add_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AddPlant.new_group_name)
        await callback.message.edit_text("🆕 Название новой группы?", reply_markup=_cancel_keyboard().as_markup())
        await track_callback(callback, state)
        return

    group_id = None if value == "none" else int(value)
    await state.update_data(group_id=group_id)
    await _ask_comment(callback, state)


@router.message(StateFilter(AddPlant.new_group_name), ~F.text.in_(MENU_BUTTONS))
async def add_new_group_name(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        group, _ = await crud.get_or_create_group(session, user_id, message.text.strip())
        await session.commit()
        group_id = group.id

    await state.update_data(group_id=group_id)
    await _ask_comment(message, state)


@router.message(Command("skip"), StateFilter(AddPlant.comment))
async def add_skip_comment(message: Message, state: FSMContext, user_id: int) -> None:
    await _finalize_add(message, state, user_id, comment=None)


@router.message(StateFilter(AddPlant.comment), ~F.text.in_(MENU_BUTTONS))
async def add_comment(message: Message, state: FSMContext, user_id: int) -> None:
    await _finalize_add(message, state, user_id, comment=message.text.strip())


async def _finalize_add(message: Message, state: FSMContext, user_id: int, comment: str | None) -> None:
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
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=tracked_id)
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=builder.as_markup())


# ---------- Удаление ----------

@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.delete()


# ---------- Удаление растения из конкретного списка ----------

@router.callback_query(F.data.startswith("lgdel:"))
async def lgdel_pick(callback: CallbackQuery, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    async with get_session() as session:
        plants = await _plants_for_token(session, user_id, token)

    await callback.answer()
    if not plants:
        await callback.answer("В этом списке пока нет растений", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plant in plants:
        builder.button(text=plant.name, callback_data=f"lgpdel:{token}:{plant.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("🗑 Какое растение удалить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpdel:"))
async def lgpdel_confirm_ask(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"lgpdelc:{token}:{plant_id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("🗑 Точно удалить это растение?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpdelc:"))
async def lgpdel_confirm(callback: CallbackQuery, user_id: int) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    async with get_session() as session:
        plant = await crud.get_plant(session, int(plant_id), user_id)
        if plant is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        await plant_service.remove_plant(session, plant)

    await callback.answer("Удалено")
    await show_group_page(callback, user_id, token, 1)


# ---------- Изменение растения из конкретного списка ----------

@router.callback_query(F.data.startswith("lgedit:"))
async def lgedit_pick(callback: CallbackQuery, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    async with get_session() as session:
        plants = await _plants_for_token(session, user_id, token)

    await callback.answer()
    if not plants:
        await callback.answer("В этом списке пока нет растений", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plant in plants:
        builder.button(text=plant.name, callback_data=f"lgpedit:{token}:{plant.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(1)
    await callback.message.edit_text("✏️ Какое растение изменить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpedit:"))
async def lgpedit_field(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="Название", callback_data=f"lgpeditf:{token}:{plant_id}:name", style="success")
    builder.button(text="Комментарий", callback_data=f"lgpeditf:{token}:{plant_id}:comment", style="primary")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(2, 1)
    await callback.message.edit_text("✏️ Что изменить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpeditf:"))
async def lgpeditf_ask_value(callback: CallbackQuery, state: FSMContext) -> None:
    _, token, plant_id, field = callback.data.split(":", 3)
    await callback.answer()
    await state.clear()
    await state.update_data(token=token, plant_id=int(plant_id), field=field)
    await state.set_state(EditPlant.value)
    prompt = "✏️ Новое название:" if field == "name" else "💬 Новый комментарий (или /skip, чтобы убрать):"
    await callback.message.edit_text(prompt)
    await track_callback(callback, state)


@router.message(Command("skip"), StateFilter(EditPlant.value))
async def edit_skip_value(message: Message, state: FSMContext, user_id: int) -> None:
    await _finalize_edit(message, state, user_id, value=None)


@router.message(StateFilter(EditPlant.value), ~F.text.in_(MENU_BUTTONS))
async def edit_value(message: Message, state: FSMContext, user_id: int) -> None:
    await _finalize_edit(message, state, user_id, value=message.text.strip())


async def _finalize_edit(message: Message, state: FSMContext, user_id: int, value: str | None) -> None:
    data = await state.get_data()
    field = data["field"]
    tracked_id = await pop_tracked(state)
    async with get_session() as session:
        plant = await crud.get_plant(session, data["plant_id"], user_id)
        if plant is None:
            await state.clear()
            await render(message, state, "⚠️ Растение уже удалено.")
            return

        if field == "name" and value:
            await crud.update_plant(session, plant, name=value)
        elif field == "comment":
            await crud.update_plant(session, plant, comment=value)
        await session.commit()

    token = data["token"]
    await state.clear()
    await send_group_page(message, user_id, token, edit_message_id=tracked_id, notice="✏️ Изменено")
