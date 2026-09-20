"""Сценарий создания зоны полива (FSM ZoneAdd): название -> интервал ->
время напоминания.

ask_interval и ask_notify_time вынесены отдельно от хендлеров шагов,
чтобы ИИ-агент мог войти в тот же диалог с уже известным названием
(bot/handlers/ai_agent/zone_flow.py) — дальше подхватывают обычные
хендлеры ZoneAdd.interval/ZoneAdd.notify_time ниже."""

from datetime import time
from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import cancel_keyboard
from bot.keyboards.reply import MENU_BUTTONS
from bot.keyboards.watering import interval_keyboard, notify_time_keyboard
from bot.services import watering_service
from bot.services.watering_service import MAX_INTERVAL_DAYS, MAX_NAME_LENGTH, MIN_INTERVAL_DAYS
from bot.utils.chat import (
    begin_dialog,
    delete_user_message,
    pop_tracked,
    render,
    safe_delete_message,
    safe_edit_text,
    track_callback,
)

from . import router
from .common import leave_zone_dialog, menu_view
from .states import ZoneAdd


@router.callback_query(F.data == "wzadd")
async def zone_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    old_msg_id = await begin_dialog(state)
    if old_msg_id and old_msg_id != callback.message.message_id:
        await safe_delete_message(callback.bot, callback.message.chat.id, old_msg_id)
    await state.set_state(ZoneAdd.name)
    await callback.message.edit_text(
        "💧 Как назвать зону полива?\n\nНапример: «Подоконник», «Суккуленты», «Балкон»",
        reply_markup=cancel_keyboard("wzcancel"),
    )
    await track_callback(callback, state)


@router.callback_query(F.data == "wzcancel")
async def zone_add_cancel(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer("Отменено")
    await leave_zone_dialog(state)
    text, kb = await menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.message(StateFilter(ZoneAdd.name), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_add_name(message: Message, state: FSMContext, user_id: int) -> None:
    name = message.text.strip()
    await delete_user_message(message)

    if not name or len(name) > MAX_NAME_LENGTH:
        await render(
            message, state, f"⚠️ Название должно быть от 1 до {MAX_NAME_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=cancel_keyboard("wzcancel"),
        )
        return

    async with get_session() as session:
        existing = await crud.get_zone_by_name(session, user_id, name)
    if existing:
        await render(
            message, state, f"⚠️ Зона «{escape(existing.name)}» уже есть. Придумай другое название.",
            reply_markup=cancel_keyboard("wzcancel"),
        )
        return

    await ask_interval(message, state, name)


async def ask_interval(message: Message, state: FSMContext, name: str) -> None:
    await state.update_data(name=name)
    await state.set_state(ZoneAdd.interval)
    await render(
        message,
        state,
        f"⏱ Как часто поливать зону «{escape(name)}»?\n\nВыбери кнопкой или напиши число дней.",
        reply_markup=interval_keyboard("wzint", "wzcancel"),
    )


@router.callback_query(StateFilter(ZoneAdd.interval), F.data.startswith("wzint:"))
async def zone_add_interval_button(callback: CallbackQuery, state: FSMContext) -> None:
    days = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await ask_notify_time(callback.message, state, days, edit=True)


@router.message(StateFilter(ZoneAdd.interval), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_add_interval_text(message: Message, state: FSMContext) -> None:
    await delete_user_message(message)
    days = watering_service.parse_interval(message.text)
    if days is None:
        await render(
            message,
            state,
            f"⚠️ Нужно число дней от {MIN_INTERVAL_DAYS} до {MAX_INTERVAL_DAYS}, например: 7",
            reply_markup=interval_keyboard("wzint", "wzcancel"),
        )
        return
    await ask_notify_time(message, state, days, edit=False)


async def ask_notify_time(message: Message, state: FSMContext, days: int, *, edit: bool) -> None:
    await state.update_data(interval_days=days)
    await state.set_state(ZoneAdd.notify_time)
    text = (
        f"🕒 В какое время присылать напоминание про полив раз в {days} дн.?\n\n"
        "Выбери кнопкой или напиши в чат собственное время."
    )
    kb = notify_time_keyboard("wztime", "wztskip", "wzcancel")
    if edit:
        await safe_edit_text(message, text, reply_markup=kb)
    else:
        await render(message, state, text, reply_markup=kb)


@router.callback_query(StateFilter(ZoneAdd.notify_time), F.data.startswith("wztime:"))
async def zone_add_notify_time_button(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    hhmm = callback.data.split(":", 1)[1]
    notify_time = time(int(hhmm[:2]), int(hhmm[2:]))
    await callback.answer()
    await _finish_add(callback.message, state, user_id, notify_time, edit=True)


@router.callback_query(StateFilter(ZoneAdd.notify_time), F.data == "wztskip")
async def zone_add_notify_time_skip(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await _finish_add(callback.message, state, user_id, None, edit=True)


@router.message(StateFilter(ZoneAdd.notify_time), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_add_notify_time_text(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    notify_time = watering_service.parse_notify_time(message.text)
    if notify_time is None:
        await render(
            message,
            state,
            "⚠️ Нужно время в формате ЧЧ:ММ, например: 09:30",
            reply_markup=notify_time_keyboard("wztime", "wztskip", "wzcancel"),
        )
        return
    await _finish_add(message, state, user_id, notify_time, edit=False)


async def _finish_add(
    message: Message, state: FSMContext, user_id: int, notify_time: time | None, *, edit: bool
) -> None:
    data = await state.get_data()
    name = data["name"]
    days = data["interval_days"]
    tracked_id = await pop_tracked(state)
    await state.clear()

    async with get_session() as session:
        try:
            zone = await watering_service.add_zone(session, user_id, name, days, notify_time)
        except watering_service.ZoneAlreadyExists:
            notice = f"⚠️ Зона «{escape(name)}» уже есть."
        else:
            when = watering_service.describe_notify_time(notify_time)
            notice = f"✅ Зона «{escape(zone.name)}» добавлена. Напомню полить через {days} дн. ({when})."

    text, kb = await menu_view(user_id, notice)
    if edit:
        await safe_edit_text(message, text, reply_markup=kb)
        return
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)
    await message.answer(text, reply_markup=kb)
