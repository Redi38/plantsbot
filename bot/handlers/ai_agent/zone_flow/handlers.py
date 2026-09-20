"""Точки входа из entrypoint.handle_free_text (handle_zone_*_intent) и
колбэки выбора зоны / подтверждения удаления.

Как и в остальных сценариях агента, значение, которое пользователь уже
назвал в тексте, применяется сразу, без лишних шагов. Исключения:

- удаление зоны — всегда через подтверждение (как удаление растения);
- создание зоны: если интервал или время напоминания не названы, агент не
  придумывает их сам, а входит в обычный диалог создания
  (bot/handlers/watering: ZoneAdd) на нужном шаге — с теми же кнопками
  выбора;
- несколько подходящих зон — сперва список на выбор, затем действие.

Все handle_zone_*_intent имеют одну сигнатуру и возвращают True, если
разобрались с запросом (в т.ч. ответили ошибкой), или False, если в
intent не хватило обязательных полей — тогда entrypoint отвечает общим
«не поняла»."""

from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import WateringZone
from bot.handlers.watering import ask_interval, ask_notify_time
from bot.keyboards.watering import zones_menu_keyboard
from bot.services import watering_service
from bot.services.watering_service import MAX_NAME_LENGTH, SNOOZE_OPTIONS

from .. import router
from ..keyboards import zone_result_keyboard
from ..states import AIZone
from .apply import apply, run_on_zone
from .intent import (
    INTERVAL_ERROR,
    NOT_FOUND,
    TIME_INVALID,
    TIME_MISSING,
    TIME_SET,
    read_days,
    read_notify_time,
    text,
)


async def handle_zone_add_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    name = text(intent, "zone_name")
    if not name:
        return False
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(f"⚠️ Название зоны должно быть от 1 до {MAX_NAME_LENGTH} символов.")
        return True
    existing = next((z for z in zones if z.name.strip().lower() == name.lower()), None)
    if existing is not None:
        await message.answer(f"💧 Зона «{escape(existing.name)}» уже есть — ничего не меняла")
        return True

    days, days_ok = read_days(intent.get("interval_days"))
    if not days_ok:
        await message.answer(INTERVAL_ERROR)
        return True
    notify_time, time_status = read_notify_time(intent.get("notify_time"))

    # Чего не хватает — то спрашиваем обычным диалогом создания зоны, теми
    # же кнопками, что и в меню «💧 Полив».
    if days is None:
        await ask_interval(message, state, name)
        return True
    if time_status != TIME_SET:
        await state.update_data(name=name)
        await ask_notify_time(message, state, days, edit=False)
        return True

    async with get_session() as session:
        try:
            zone = await watering_service.add_zone(session, user_id, name, days, notify_time)
        except watering_service.ZoneAlreadyExists:
            await message.answer(f"💧 Зона «{escape(name)}» уже есть — ничего не меняла")
            return True
        except ValueError:
            await message.answer(f"⚠️ Название зоны должно быть от 1 до {MAX_NAME_LENGTH} символов.")
            return True
    when = watering_service.describe_notify_time(notify_time)
    await message.answer(
        f"✅ Зона «{escape(zone.name)}» добавлена. Напомню полить через {days} дн. ({when}).",
        reply_markup=zone_result_keyboard(zone.id).as_markup(),
    )
    return True


async def handle_zone_water_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), {"op": "water"})


async def handle_zone_snooze_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    days, ok = read_days(intent.get("snooze_days"))
    if not ok:
        await message.answer(INTERVAL_ERROR)
        return True
    # Срок не назван — откладываем на самый короткий вариант из кнопок
    # под напоминанием; итоговый срок виден в ответе.
    params = {"op": "snooze", "days": days or SNOOZE_OPTIONS[0]}
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), params)


async def handle_zone_interval_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    days, ok = read_days(intent.get("interval_days"))
    if not ok:
        await message.answer(INTERVAL_ERROR)
        return True
    if days is None:
        return False
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), {"op": "interval", "days": days})


async def handle_zone_time_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    notify_time, status = read_notify_time(intent.get("notify_time"))
    if status == TIME_MISSING:
        return False
    if status == TIME_INVALID:
        await message.answer("⚠️ Не поняла время. Напиши в формате ЧЧ:ММ, например: 09:30")
        return True
    hhmm = notify_time.strftime("%H%M") if notify_time else None
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), {"op": "time", "notify_time": hhmm})


async def handle_zone_rename_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    new_name = text(intent, "new_name")
    if not new_name:
        return False
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), {"op": "rename", "new_name": new_name})


async def handle_zone_delete_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    return await run_on_zone(message, state, user_id, zones, text(intent, "zone_name"), {"op": "delete"})


async def handle_zone_list_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    """Без названия — общий обзор зон (как в меню «💧 Полив»), с названием —
    карточка одной зоны."""
    query = text(intent, "zone_name")
    if query:
        return await run_on_zone(message, state, user_id, zones, query, {"op": "card"})
    now = watering_service.utcnow()
    await message.answer(watering_service.render_overview(zones, now), reply_markup=zones_menu_keyboard(zones, now))
    return True


# ---------- Колбэки: выбор зоны и подтверждение удаления ----------


@router.callback_query(StateFilter(AIZone.pick_zone), F.data.startswith("aizpick:"))
async def ai_zone_pick(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    zone_id = int(callback.data.split(":", 1)[1])
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    await apply(callback, state, user_id, zone_id, data["params"])


@router.callback_query(StateFilter(AIZone.confirm_delete), F.data == "aizdelconfirm")
async def ai_zone_delete_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        zone = await crud.get_zone(session, data.get("zone_id"), user_id)
        if zone is None:
            await callback.message.edit_text(NOT_FOUND)
            return
        name = escape(zone.name)
        await watering_service.remove(session, zone)
    await callback.message.edit_text(f"🗑 Удалила зону «{name}»")


@router.callback_query(StateFilter(AIZone.pick_zone, AIZone.confirm_delete), F.data == "aizcancel")
async def ai_zone_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
