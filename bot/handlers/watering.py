"""Зоны полива: меню «💧 Полив», создание/просмотр/удаление зоны, смена
интервала и кнопки под напоминанием («Полил» / «Отложить»).

Сами напоминания шлёт фоновая задача (bot/services/watering_reminders.py);
здесь только реакция на нажатия. Кнопки под напоминанием (wzdone/wzlate)
намеренно отдельные от кнопок карточки зоны (wzwater): напоминание — это
одноразовое сообщение, после ответа его текст заменяется итогом без
клавиатуры, а карточка после действия перерисовывается сама.

Роутер регистрируется в диспетчере ДО ai_agent — тот ловит любой
свободный текст вне FSM."""

from datetime import time
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import confirm_delete_keyboard
from bot.keyboards.reply import BTN_WATER, MENU_BUTTONS
from bot.keyboards.watering import (
    cancel_keyboard,
    interval_keyboard,
    notify_time_keyboard,
    zone_card_keyboard,
    zones_menu_keyboard,
)
from bot.services import watering_service
from bot.services.watering_service import MAX_INTERVAL_DAYS, MAX_NAME_LENGTH, MIN_INTERVAL_DAYS, SNOOZE_OPTIONS
from bot.utils.chat import (
    begin_dialog,
    delete_user_message,
    pop_tracked,
    render,
    safe_delete_message,
    safe_edit_text,
    track_callback,
)

router = Router(name="watering")

_NOT_FOUND = "⚠️ Зона не найдена, возможно уже удалена."


class ZoneAdd(StatesGroup):
    name = State()
    interval = State()
    notify_time = State()


class ZoneEdit(StatesGroup):
    name = State()
    interval = State()
    notify_time = State()


# ---------- Общие представления ----------


async def _menu_view(user_id: int, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    now = watering_service.utcnow()
    async with get_session() as session:
        zones = await crud.list_zones(session, user_id)
    text = watering_service.render_overview(zones, now)
    if notice:
        text = f"{notice}\n\n{text}"
    return text, zones_menu_keyboard(zones, now)


async def _card_view(user_id: int, zone_id: int, notice: str | None = None) -> tuple[str, InlineKeyboardMarkup] | None:
    now = watering_service.utcnow()
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
    if zone is None:
        return None
    text = watering_service.render_card(zone, now)
    if notice:
        text = f"{notice}\n\n{text}"
    return text, zone_card_keyboard(zone.id)


async def _edit_to_card(message: Message, user_id: int, zone_id: int, notice: str | None = None) -> None:
    view = await _card_view(user_id, zone_id, notice)
    if view is None:
        await safe_edit_text(message, _NOT_FOUND)
        return
    text, kb = view
    await safe_edit_text(message, text, reply_markup=kb)


async def _leave_zone_dialog(state: FSMContext) -> None:
    """Сбрасывает состояние, только если оно принадлежит нашим диалогам:
    иначе кнопка «Назад» из карточки зоны могла бы случайно оборвать
    чужой сценарий (добавление растения и т.п.)."""
    current = await state.get_state() or ""
    if current.startswith(("ZoneAdd:", "ZoneEdit:")):
        await state.clear()


# ---------- Меню ----------


@router.message(F.text == BTN_WATER)
async def cmd_water(message: Message, state: FSMContext, user_id: int) -> None:
    await delete_user_message(message)
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        await safe_delete_message(message.bot, message.chat.id, old_msg_id)
    text, kb = await _menu_view(user_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "wzmenu")
async def zones_back(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await _leave_zone_dialog(state)
    text, kb = await _menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.callback_query(F.data.startswith("wz:"))
async def zone_open(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await _leave_zone_dialog(state)
    await _edit_to_card(callback.message, user_id, zone_id)


# ---------- Создание зоны ----------


@router.callback_query(F.data == "wzadd")
async def zone_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    old_msg_id = await begin_dialog(state)
    if old_msg_id and old_msg_id != callback.message.message_id:
        await safe_delete_message(callback.bot, callback.message.chat.id, old_msg_id)
    await state.set_state(ZoneAdd.name)
    await callback.message.edit_text(
        "💧 Как назвать зону полива?\n\nНапример: «Подоконник», «Суккуленты», «Балкон»",
        reply_markup=cancel_keyboard(),
    )
    await track_callback(callback, state)


@router.callback_query(F.data == "wzcancel")
async def zone_add_cancel(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer("Отменено")
    await _leave_zone_dialog(state)
    text, kb = await _menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


@router.message(StateFilter(ZoneAdd.name), F.text, ~F.text.in_(MENU_BUTTONS))
async def zone_add_name(message: Message, state: FSMContext, user_id: int) -> None:
    name = message.text.strip()
    await delete_user_message(message)

    if not name or len(name) > MAX_NAME_LENGTH:
        await render(
            message, state, f"⚠️ Название должно быть от 1 до {MAX_NAME_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=cancel_keyboard(),
        )
        return

    async with get_session() as session:
        existing = await crud.get_zone_by_name(session, user_id, name)
    if existing:
        await render(
            message, state, f"⚠️ Зона «{escape(existing.name)}» уже есть. Придумай другое название.",
            reply_markup=cancel_keyboard(),
        )
        return

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
    await _ask_notify_time(callback.message, state, days, edit=True)


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
    await _ask_notify_time(message, state, days, edit=False)


async def _ask_notify_time(message: Message, state: FSMContext, days: int, *, edit: bool) -> None:
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

    text, kb = await _menu_view(user_id, notice)
    if edit:
        await safe_edit_text(message, text, reply_markup=kb)
        return
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)
    await message.answer(text, reply_markup=kb)


# ---------- Действия с зоной ----------


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
    await _edit_to_card(callback.message, user_id, zone_id, notice="✅ Полив записан")


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
    text, kb = await _menu_view(user_id, f"✅ Зона «{escape(name)}» удалена.")
    await safe_edit_text(callback.message, text, reply_markup=kb)


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
        await message.answer(_NOT_FOUND)
        return
    view = await _card_view(user_id, zone_id, notice=f"✅ Название изменено на «{escape(name)}».")
    if view is None:
        await message.answer(_NOT_FOUND)
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
    await _leave_zone_dialog(state)
    if not await _apply_interval(user_id, int(zone_id), int(days)):
        await safe_edit_text(callback.message, _NOT_FOUND)
        return
    await _edit_to_card(callback.message, user_id, int(zone_id), notice=f"✅ Теперь поливаем раз в {days} дн.")


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
        await message.answer(_NOT_FOUND)
        return
    view = await _card_view(user_id, zone_id, notice=f"✅ Теперь поливаем раз в {days} дн.")
    if view is None:
        await message.answer(_NOT_FOUND)
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
    await _leave_zone_dialog(state)
    if not await _apply_notify_time(user_id, int(zone_id), notify_time):
        await safe_edit_text(callback.message, _NOT_FOUND)
        return
    await _edit_to_card(
        callback.message,
        user_id,
        int(zone_id),
        notice=f"✅ Теперь напоминаю {watering_service.describe_notify_time(notify_time)}.",
    )


@router.callback_query(F.data.startswith("wztskip:"))
async def zone_edit_notify_time_skip(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    await _leave_zone_dialog(state)
    if not await _apply_notify_time(user_id, zone_id, None):
        await safe_edit_text(callback.message, _NOT_FOUND)
        return
    await _edit_to_card(callback.message, user_id, zone_id, notice="✅ Фиксированный час напоминания убран.")


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

    tracked_id = await pop_tracked(state)
    await state.clear()
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)

    if not await _apply_notify_time(user_id, zone_id, notify_time):
        await message.answer(_NOT_FOUND)
        return
    view = await _card_view(user_id, zone_id, notice=f"✅ Теперь напоминаю {watering_service.describe_notify_time(notify_time)}.")
    if view is None:
        await message.answer(_NOT_FOUND)
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)


# ---------- Кнопки под напоминанием ----------


@router.callback_query(F.data.startswith("wzdone:"))
async def reminder_done(callback: CallbackQuery, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await callback.answer("Зона уже удалена", show_alert=True)
            await safe_edit_text(callback.message, _NOT_FOUND)
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
            await safe_edit_text(callback.message, _NOT_FOUND)
            return
        name = zone.name
        await watering_service.snooze(session, zone, days)
    await callback.answer("Отложено")
    await safe_edit_text(
        callback.message, f"⏰ Отложено на {days} дн. Напомню про зону «{escape(name)}» позже."
    )
