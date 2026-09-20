"""Действия с зоной (полить сейчас / удалить) и кнопки под напоминанием
(«Полил» / «Отложить»).

Кнопки под напоминанием (wzdone/wzlate) намеренно отдельные от кнопок
карточки зоны (wzwater): напоминание — это одноразовое сообщение, после
ответа его текст заменяется итогом без клавиатуры, а карточка после
действия перерисовывается сама."""

from html import escape

from aiogram import F
from aiogram.types import CallbackQuery

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import confirm_delete_keyboard
from bot.services import watering_service
from bot.services.watering_service import SNOOZE_OPTIONS
from bot.utils.chat import safe_edit_text

from . import router
from .common import NOT_FOUND, edit_to_card, menu_view


@router.callback_query(F.data.startswith("wzwaterask:"))
async def zone_water_ask(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    kb = confirm_delete_keyboard(
        f"wzwater:{zone_id}",
        f"wz:{zone_id}",
        confirm_label="✅ Да",
        confirm_style="success",
        cancel_label="⬅️ Назад",
        cancel_style="primary",
    )
    await safe_edit_text(callback.message, f"Вы полили зону «{escape(zone.name)}»?", reply_markup=kb)


@router.callback_query(F.data.startswith("wzwater:"))
async def zone_water(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await callback.answer("Зона уже удалена", show_alert=True)
            return
        await watering_service.mark_watered(session, zone)
    await callback.answer("Записано")
    await edit_to_card(callback.message, user_id, zone_id, notice="✅ Полив записан")


@router.callback_query(F.data.startswith("wzdel:"))
async def zone_delete_ask(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    kb = confirm_delete_keyboard(
        f"wzdelc:{zone_id}",
        f"wz:{zone_id}",
        confirm_label="🗑 Да, удалить",
        cancel_label="⬅️ Назад",
        cancel_style="primary",
    )
    await safe_edit_text(
        callback.message, f"🗑 Удалить зону «{escape(zone.name)}»? Напоминания о ней приходить перестанут.", reply_markup=kb
    )


@router.callback_query(F.data.startswith("wzdelc:"))
async def zone_delete_apply(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await callback.answer("Уже удалено", show_alert=True)
            return
        name = zone.name
        await watering_service.remove(session, zone)
    await callback.answer("Удалено")
    text, kb = await menu_view(user_id, f"✅ Зона «{escape(name)}» удалена.")
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.startswith("wzdone:"))
async def reminder_done(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await callback.answer("Зона уже удалена", show_alert=True)
            await safe_edit_text(callback.message, NOT_FOUND)
            return
        name, interval = zone.name, zone.interval_days
        await watering_service.mark_watered(session, zone)
    await callback.answer("Записано")
    await safe_edit_text(
        callback.message, f"✅ Зона «{escape(name)}» полита. Следующий полив через {interval} дн."
    )


@router.callback_query(F.data.startswith("wzlate:"))
async def reminder_snooze(callback: CallbackQuery, user_id: int) -> None:
    _, zone_id, days_raw = callback.data.split(":")
    days = int(days_raw)
    if days not in SNOOZE_OPTIONS:
        await callback.answer()
        return
    async with get_session() as session:
        zone = await crud.get_zone(session, int(zone_id), user_id)
        if zone is None:
            await callback.answer("Зона уже удалена", show_alert=True)
            await safe_edit_text(callback.message, NOT_FOUND)
            return
        name = zone.name
        await watering_service.snooze(session, zone, days)
    await callback.answer("Отложено")
    await safe_edit_text(
        callback.message, f"⏰ Отложено на {days} дн. Напомню про зону «{escape(name)}» позже."
    )
