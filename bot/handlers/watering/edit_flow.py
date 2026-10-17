"""Сценарии изменения существующей зоны (FSM ZoneEdit): переименование,
смена интервала, смена времени напоминания, смена даты следующего полива.
Четыре независимых входа (wzrename/wzedit/wztedit/wzdate), объединённые общим состоянием, потому что ни
один из них не пересекается с другими по экрану."""

from datetime import date, time, timedelta
from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import cancel_keyboard
from bot.keyboards.reply import MENU_BUTTONS
from bot.keyboards.watering import interval_keyboard, next_date_keyboard, notify_time_keyboard
from bot.services import watering_service
from bot.services.watering_service import MAX_INTERVAL_DAYS, MAX_NAME_LENGTH, MIN_INTERVAL_DAYS
from bot.utils.chat import delete_user_message, pop_tracked, render, safe_delete_message, safe_edit_text, track_callback

from . import router
from .common import NOT_FOUND, card_view, edit_to_card, leave_zone_dialog
from .states import ZoneEdit

# ---------- Смена названия ----------


@router.callback_query(F.data.startswith("wzrename:"))
async def zone_rename_start(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(zone_id=zone_id)
    await state.set_state(ZoneEdit.name)
    await callback.message.edit_text(
        f"✏️ Новое название для зоны «{escape(zone.name)}»?",
        reply_markup=cancel_keyboard(f"wz:{zone_id}", label="⬅️ Назад", style="primary"),
    )
    await track_callback(callback, state)


async def _apply_rename(user_id: int, zone_id: int, name: str) -> tuple[bool, str | None]:
    """(найдена_ли_зона, текст_ошибки_валидации). Если зона не найдена —
    (False, None); если найдена, но имя не подошло — (False, "⚠️ ...");
    при успехе — (True, None)."""
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return False, None
        try:
            await watering_service.rename(session, zone, name)
        except watering_service.ZoneAlreadyExists:
            return False, f"⚠️ Зона «{escape(name.strip())}» уже есть. Придумай другое название."
        except ValueError:
            return False, f"⚠️ Название должно быть от 1 до {MAX_NAME_LENGTH} символов. Попробуй ещё раз."
    return True, None


@router.message(StateFilter(ZoneEdit.name), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_rename_apply(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    zone_id = data["zone_id"]
    name = message.text.strip()

    ok, err = await _apply_rename(user_id, zone_id, name)
    if err is not None:
        await render(
            message,
            state,
            err,
            reply_markup=cancel_keyboard(f"wz:{zone_id}", label="⬅️ Назад", style="primary"),
        )
        return

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)

    if not ok:
        await message.answer(NOT_FOUND)
        return
    view = await card_view(user_id, zone_id, notice=f"✅ Название изменено на «{escape(name)}».")
    if view is None:
        await message.answer(NOT_FOUND)
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)


# ---------- Смена интервала ----------


@router.callback_query(F.data.startswith("wzedit:"))
async def zone_edit_start(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(zone_id=zone_id)
    await state.set_state(ZoneEdit.interval)
    await callback.message.edit_text(
        f"⏱ Как часто поливать зону «{escape(zone.name)}»? Сейчас — раз в {zone.interval_days} дн.\n\n"
        "Выбери кнопкой или напиши число дней. Отсчёт до следующего полива начнётся заново.",
        reply_markup=interval_keyboard(f"wzeint:{zone_id}", f"wz:{zone_id}", back_label="⬅️ Назад", back_style="primary"),
    )
    await track_callback(callback, state)


async def _apply_interval(user_id: int, zone_id: int, days: int) -> bool:
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return False
        await watering_service.set_interval(session, zone, days)
    return True


@router.callback_query(F.data.startswith("wzeint:"))
async def zone_edit_interval_button(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    _, zone_id, days = callback.data.split(":")
    await callback.answer()
    await leave_zone_dialog(state)
    if not await _apply_interval(user_id, int(zone_id), int(days)):
        await safe_edit_text(callback.message, NOT_FOUND)
        return
    await edit_to_card(callback.message, user_id, int(zone_id), notice=f"✅ Теперь поливаем раз в {days} дн.")


@router.message(StateFilter(ZoneEdit.interval), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_edit_interval_text(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    zone_id = data["zone_id"]

    days = watering_service.parse_interval(message.text)
    if days is None:
        await render(
            message,
            state,
            f"⚠️ Нужно число дней от {MIN_INTERVAL_DAYS} до {MAX_INTERVAL_DAYS}, например: 7",
            reply_markup=interval_keyboard(
                f"wzeint:{zone_id}", f"wz:{zone_id}", back_label="⬅️ Назад", back_style="primary"
            ),
        )
        return

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)

    if not await _apply_interval(user_id, zone_id, days):
        await message.answer(NOT_FOUND)
        return
    view = await card_view(user_id, zone_id, notice=f"✅ Теперь поливаем раз в {days} дн.")
    if view is None:
        await message.answer(NOT_FOUND)
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)


# ---------- Смена времени напоминания ----------


@router.callback_query(F.data.startswith("wztedit:"))
async def zone_edit_time_start(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(zone_id=zone_id)
    await state.set_state(ZoneEdit.notify_time)
    await callback.message.edit_text(
        f"🕒 В какое время присылать напоминание про зону «{escape(zone.name)}»?\n\n"
        f"Сейчас — {watering_service.describe_notify_time(zone.notify_time)}.\n"
        "Выбери кнопкой или напиши в чат собственное время.",
        reply_markup=notify_time_keyboard(
            f"wzetime:{zone_id}", f"wztskip:{zone_id}", f"wz:{zone_id}", back_label="⬅️ Назад", back_style="primary"
        ),
    )
    await track_callback(callback, state)


async def _apply_notify_time(user_id: int, zone_id: int, notify_time: time | None) -> bool:
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return False
        await watering_service.set_notify_time(session, zone, notify_time)
    return True


@router.callback_query(F.data.startswith("wzetime:"))
async def zone_edit_notify_time_button(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    _, zone_id, hhmm = callback.data.split(":")
    notify_time = time(int(hhmm[:2]), int(hhmm[2:]))
    await callback.answer()
    await leave_zone_dialog(state)
    if not await _apply_notify_time(user_id, int(zone_id), notify_time):
        await safe_edit_text(callback.message, NOT_FOUND)
        return
    await edit_to_card(
        callback.message,
        user_id,
        int(zone_id),
        notice=f"✅ Теперь напоминаю {watering_service.describe_notify_time(notify_time)}.",
    )


@router.callback_query(F.data.startswith("wztskip:"))
async def zone_edit_notify_time_skip(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await leave_zone_dialog(state)
    if not await _apply_notify_time(user_id, zone_id, None):
        await safe_edit_text(callback.message, NOT_FOUND)
        return
    await edit_to_card(callback.message, user_id, zone_id, notice="✅ Фиксированный час напоминания убран.")


@router.message(StateFilter(ZoneEdit.notify_time), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_edit_notify_time_text(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    zone_id = data["zone_id"]

    notify_time = watering_service.parse_notify_time(message.text)
    if notify_time is None:
        await render(
            message,
            state,
            "⚠️ Нужно время в формате ЧЧ:ММ, например: 09:30",
            reply_markup=notify_time_keyboard(
                f"wzetime:{zone_id}", f"wztskip:{zone_id}", f"wz:{zone_id}", back_label="⬅️ Назад", back_style="primary"
            ),
        )
        return

    # Пользователь вводит локальное время (Минск, UTC+3), в БД храним UTC.
    notify_time = watering_service.from_display_time(notify_time)

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)

    if not await _apply_notify_time(user_id, zone_id, notify_time):
        await message.answer(NOT_FOUND)
        return
    view = await card_view(user_id, zone_id, notice=f"✅ Теперь напоминаю {watering_service.describe_notify_time(notify_time)}.")
    if view is None:
        await message.answer(NOT_FOUND)
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)


# ---------- Смена даты следующего полива ----------


def _date_keyboard(zone_id: int):
    return next_date_keyboard(
        f"wzedate:{zone_id}",
        f"wz:{zone_id}",
        watering_service.local_today(watering_service.utcnow()),
        back_label="⬅️ Назад",
        back_style="primary",
    )


@router.callback_query(F.data.startswith("wzdate:"))
async def zone_edit_date_start(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        await callback.answer("Зона уже удалена", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.update_data(zone_id=zone_id)
    await state.set_state(ZoneEdit.next_date)
    await callback.message.edit_text(
        f"📅 Когда поливать зону «{escape(zone.name)}» в следующий раз?\n\n"
        f"Сейчас — {watering_service.describe_next_date(zone, watering_service.utcnow())}.\n"
        "Выбери кнопкой или напиши дату в чат, например: 20.10. "
        "Дальше напоминания пойдут по обычному интервалу от этой даты.",
        reply_markup=_date_keyboard(zone_id),
    )
    await track_callback(callback, state)


async def _apply_next_date(user_id: int, zone_id: int, local_date: date) -> bool:
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return False
        await watering_service.set_next_watering(
            session, zone, watering_service.next_watering_for_date(zone, local_date)
        )
    return True


def _date_notice(local_date: date) -> str:
    return f"✅ Следующий полив — {local_date.strftime('%d.%m.%Y')}."


@router.callback_query(F.data.startswith("wzedate:"))
async def zone_edit_date_button(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    _, zone_id, offset = callback.data.split(":")
    await callback.answer()
    await leave_zone_dialog(state)
    local_date = watering_service.local_today(watering_service.utcnow()) + timedelta(days=int(offset))
    if not await _apply_next_date(user_id, int(zone_id), local_date):
        await safe_edit_text(callback.message, NOT_FOUND)
        return
    await edit_to_card(callback.message, user_id, int(zone_id), notice=_date_notice(local_date))


@router.message(StateFilter(ZoneEdit.next_date), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_edit_date_text(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    data = await state.get_data()
    zone_id = data["zone_id"]

    now = watering_service.utcnow()
    local_date = watering_service.parse_date(message.text, now)
    if local_date is None or not watering_service.date_in_range(local_date, now):
        await render(
            message,
            state,
            f"⚠️ Нужна дата от сегодня до {watering_service.MAX_INTERVAL_DAYS} дн. вперёд "
            "в формате ДД.ММ или ДД.ММ.ГГГГ, например: 20.10",
            reply_markup=_date_keyboard(zone_id),
        )
        return

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)

    if not await _apply_next_date(user_id, zone_id, local_date):
        await message.answer(NOT_FOUND)
        return
    view = await card_view(user_id, zone_id, notice=_date_notice(local_date))
    if view is None:
        await message.answer(NOT_FOUND)
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)
