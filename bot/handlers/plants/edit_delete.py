from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.handlers.list_view import send_group_page, show_group_page
from bot.keyboards.inline import confirm_delete_keyboard
from bot.keyboards.reply import MENU_BUTTONS
from bot.services import plant_service
from bot.utils.chat import pop_tracked, render, safe_edit_text, track_callback

from . import router
from .common import PLANT_PICK_PAGE_SIZE, paginate, plant_pick_keyboard, plants_for_token


class EditPlant(StatesGroup):
    value = State()


@router.callback_query(F.data == "cancel")
async def cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.delete()


# ---------- Выбор растения из конкретного списка (для удаления/изменения) ----------

async def _show_plant_pick(
    callback: CallbackQuery, user_id: int, token: str, page: int, *, action: str, prompt: str
) -> None:
    """Показывает страницу выбора растения для удаления ('del') или
    изменения ('edit') из списка token. Общая для lgdel*/lgedit*, чтобы
    пагинация и текст кнопок не расходились между двумя похожими флоу."""
    async with get_session() as session:
        plants = await plants_for_token(session, user_id, token)

    if not plants:
        await callback.answer("В этом списке пока нет растений", show_alert=True)
        return

    item_prefix = "lgpdel" if action == "del" else "lgpedit"
    page_prefix = "lgdelpage" if action == "del" else "lgeditpage"

    items_page, page, total_pages = paginate(plants, page, PLANT_PICK_PAGE_SIZE)
    kb = plant_pick_keyboard(item_prefix, page_prefix, token, items_page, page, total_pages, back_data=f"lg:{token}")
    await safe_edit_text(callback.message, prompt, reply_markup=kb)


# ---------- Удаление растения из конкретного списка ----------

@router.callback_query(F.data.startswith("lgdel:"))
async def lgdel_pick(callback: CallbackQuery, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    await callback.answer()
    await _show_plant_pick(callback, user_id, token, 1, action="del", prompt="🗑 Какое растение удалить?")


@router.callback_query(F.data.startswith("lgdelpage:"))
async def lgdel_page(callback: CallbackQuery, user_id: int) -> None:
    _, token, page = callback.data.split(":", 2)
    await callback.answer()
    await _show_plant_pick(callback, user_id, token, int(page), action="del", prompt="🗑 Какое растение удалить?")


@router.callback_query(F.data.startswith("lgpdel:"))
async def lgpdel_confirm_ask(callback: CallbackQuery) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    kb = confirm_delete_keyboard(
        f"lgpdelc:{token}:{plant_id}", f"lg:{token}", cancel_label="⬅️ Назад", cancel_style="primary"
    )
    await callback.message.edit_text("🗑 Точно удалить это растение?", reply_markup=kb)


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
    await callback.answer()
    await _show_plant_pick(callback, user_id, token, 1, action="edit", prompt="✏️ Какое растение изменить?")


@router.callback_query(F.data.startswith("lgeditpage:"))
async def lgedit_page(callback: CallbackQuery, user_id: int) -> None:
    _, token, page = callback.data.split(":", 2)
    await callback.answer()
    await _show_plant_pick(callback, user_id, token, int(page), action="edit", prompt="✏️ Какое растение изменить?")


@router.callback_query(F.data.startswith("lgpedit:"))
async def lgpedit_field(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    _, token, plant_id = callback.data.split(":", 2)
    await callback.answer()
    await state.clear()

    async with get_session() as session:
        plant = await crud.get_plant(session, int(plant_id), user_id)
    if plant is None:
        await callback.message.edit_text("⚠️ Растение уже удалено, возможно, кем-то другим.")
        return
    plant_label = f"{plant.name} ({plant.comment})" if plant.comment else plant.name

    builder = InlineKeyboardBuilder()
    builder.button(text="Название", callback_data=f"lgpeditf:{token}:{plant_id}:name", style="success")
    builder.button(text="Комментарий", callback_data=f"lgpeditf:{token}:{plant_id}:comment", style="primary")
    builder.button(text="⬅️ Назад", callback_data=f"lg:{token}", style="primary")
    builder.adjust(2, 1)
    await callback.message.edit_text(f"✏️ «{plant_label}» — что изменить?", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("lgpeditf:"))
async def lgpeditf_ask_value(callback: CallbackQuery, state: FSMContext) -> None:
    _, token, plant_id, field = callback.data.split(":", 3)
    await callback.answer()
    await state.clear()
    await state.update_data(token=token, plant_id=int(plant_id), field=field)
    await state.set_state(EditPlant.value)
    prompt = "✏️ Новое название:" if field == "name" else "💬 Новый комментарий (или /skip, чтобы убрать):"
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data=f"lgpedit:{token}:{plant_id}", style="primary")
    await callback.message.edit_text(prompt, reply_markup=builder.as_markup())
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
