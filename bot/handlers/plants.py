from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import groups_keyboard, plant_delete_keyboard
from bot.services import plant_service

router = Router(name="plants")


class AddPlant(StatesGroup):
    name = State()
    group = State()
    new_group_name = State()
    comment = State()


# ---------- Добавление ----------

@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddPlant.name)
    await message.answer("Как называется растение?")


@router.message(StateFilter(AddPlant.name))
async def add_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        groups = await crud.list_groups(session, user.id)

    await state.set_state(AddPlant.group)
    if groups:
        await message.answer("Выбери группу:", reply_markup=groups_keyboard(groups, prefix="addgroup"))
    else:
        await state.update_data(group_id=None)
        await state.set_state(AddPlant.comment)
        await message.answer("Групп пока нет — добавлю без группы. Комментарий есть? (или /skip)")


@router.callback_query(StateFilter(AddPlant.group), F.data.startswith("addgroup:"))
async def add_choose_group(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AddPlant.new_group_name)
        await callback.message.edit_text("Название новой группы?")
        return

    if value == "none":
        await state.update_data(group_id=None)
    else:
        await state.update_data(group_id=int(value))

    await state.set_state(AddPlant.comment)
    await callback.message.edit_text("Комментарий есть? Напиши текстом или пришли /skip")


@router.message(StateFilter(AddPlant.new_group_name))
async def add_new_group_name(message: Message, state: FSMContext) -> None:
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        group, _ = await crud.get_or_create_group(session, user.id, message.text.strip())
        await session.commit()

    await state.update_data(group_id=group.id)
    await state.set_state(AddPlant.comment)
    await message.answer("Комментарий есть? Напиши текстом или пришли /skip")


@router.message(Command("skip"), StateFilter(AddPlant.comment))
async def add_skip_comment(message: Message, state: FSMContext) -> None:
    await _finalize_add(message, state, comment=None)


@router.message(StateFilter(AddPlant.comment))
async def add_comment(message: Message, state: FSMContext) -> None:
    await _finalize_add(message, state, comment=message.text.strip())


async def _finalize_add(message: Message, state: FSMContext, comment: str | None) -> None:
    data = await state.get_data()
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        plant = await crud.create_plant(
            session, user.id, data["name"], group_id=data.get("group_id"), comment=comment
        )
        await session.commit()

    await state.clear()
    await message.answer(f"🌱 Добавила «{plant.name}»")


# ---------- Удаление ----------

@router.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        groups, ungrouped = await crud.get_full_tree(session, user.id)

    all_plants = ungrouped[:]
    for g in groups:
        all_plants.extend(g.plants)

    if not all_plants:
        await message.answer("Растений пока нет.")
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    for plant in all_plants:
        builder.button(text=plant.name, callback_data=f"plant_delete:{plant.id}")
    builder.adjust(1)
    await message.answer("Какое растение удалить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("plant_delete:"))
async def plant_delete_ask(callback: CallbackQuery) -> None:
    plant_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await callback.message.edit_text(
        "Точно удалить это растение?", reply_markup=plant_delete_keyboard(plant_id)
    )


@router.callback_query(F.data.startswith("plant_delete_confirm:"))
async def plant_delete_confirm(callback: CallbackQuery) -> None:
    plant_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        user = await crud.get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        plant = await crud.get_plant(session, plant_id, user.id)
        if plant is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        name = plant.name
        await plant_service.remove_plant(session, plant)

    await callback.answer()
    await callback.message.edit_text(f"🗑 Удалила «{name}»")


@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.edit_text("Отменено")
