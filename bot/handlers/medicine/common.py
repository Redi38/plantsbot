"""Общие представления и хелперы аптечки (меню, карточка, выход из диалога)."""

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.medicine import medicine_card_keyboard, medicines_menu_keyboard
from bot.services import medicine_service
from bot.utils.chat import safe_edit_text

NOT_FOUND = "⚠️ Препарат не найден, возможно уже удалён."


async def menu_view(user_id: int, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    today = medicine_service.today_utc()
    async with get_session() as session:
        medicines = await crud.list_medicines(session, user_id)
    text = medicine_service.render_overview(medicines, today)
    if notice:
        text = f"{notice}\n\n{text}"
    return text, medicines_menu_keyboard(medicines, today)


async def card_view(user_id: int, medicine_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
    if medicine is None:
        return None
    return medicine_service.render_card(medicine, medicine_service.today_utc()), medicine_card_keyboard(medicine.id)


async def edit_to_card(message: Message, user_id: int, medicine_id: int) -> None:
    view = await card_view(user_id, medicine_id)
    if view is None:
        await safe_edit_text(message, NOT_FOUND)
        return
    text, kb = view
    await safe_edit_text(message, text, reply_markup=kb)


async def leave_dialog(state: FSMContext) -> None:
    """Сбрасывает состояние, только если оно принадлежит диалогу аптечки:
    иначе кнопка «Назад» из карточки могла бы оборвать чужой сценарий."""
    current = await state.get_state() or ""
    if current.startswith("MedAdd:"):
        await state.clear()
