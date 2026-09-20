"""Общие представления и хелперы, используемые несколькими сценариями
зон полива (меню, карточка зоны, выход из диалога)."""

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.watering import zone_card_keyboard, zones_menu_keyboard
from bot.services import watering_service
from bot.utils.chat import safe_edit_text

NOT_FOUND = "⚠️ Зона не найдена, возможно уже удалена."


async def menu_view(user_id: int, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    now = watering_service.utcnow()
    async with get_session() as session:
        zones = await crud.list_zones(session, user_id)
    text = watering_service.render_overview(zones, now)
    if notice:
        text = f"{notice}\n\n{text}"
    return text, zones_menu_keyboard(zones, now)


async def card_view(user_id: int, zone_id: int, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup] | None:
    now = watering_service.utcnow()
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        return None
    text = watering_service.render_card(zone, now)
    if notice:
        text = f"{notice}\n\n{text}"
    return text, zone_card_keyboard(zone.id)


async def edit_to_card(message: Message, user_id: int, zone_id: int, notice: str | None = None) -> None:
    view = await card_view(user_id, zone_id, notice)
    if view is None:
        await safe_edit_text(message, NOT_FOUND)
        return
    text, kb = view
    await safe_edit_text(message, text, reply_markup=kb)


async def leave_zone_dialog(state: FSMContext) -> None:
    """Сбрасывает состояние, только если оно принадлежит нашим диалогам:
    иначе кнопка «Назад» из карточки зоны могла бы случайно оборвать
    чужой сценарий (добавление растения и т.п.)."""
    current = await state.get_state() or ""
    if current.startswith(("ZoneAdd:", "ZoneEdit:")):
        await state.clear()
