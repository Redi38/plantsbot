"""Зоны полива через ИИ-агента — те же возможности, что и в меню «💧 Полив»:
создать зону, отметить полив, отложить, сменить интервал / время напоминания /
название, удалить, показать список или карточку одной зоны.

Логика сценариев (то, что делают кнопки) уже лежит в
bot/services/watering_service.py — здесь только разбор полей intent, поиск
зоны по названию и вызов тех же функций сервиса. Поэтому поведение
совпадает с ручным меню: интервал считается от момента действия, «полил»
пересчитывает срок от сейчас и т.д.

Как и в остальных сценариях агента, значение, которое пользователь уже
назвал в тексте, применяется сразу, без лишних шагов. Исключения:

- удаление зоны — всегда через подтверждение (как удаление растения);
- создание зоны: если интервал или время напоминания не названы, агент не
  придумывает их сам, а входит в обычный диалог создания (bot/handlers/
  watering.py: ZoneAdd) на нужном шаге — с теми же кнопками выбора;
- несколько подходящих зон — сперва список на выбор, затем действие.

Время напоминания модель возвращает в том виде, как его назвал пользователь
(локальное, Минск UTC+3, как на кнопках), а в БД оно хранится в UTC —
конвертация здесь, через watering_service.from_display_time.

Все хендлеры handle_zone_*_intent имеют одну сигнатуру и возвращают True,
если разобрались с запросом (в т.ч. ответили ошибкой), или False, если в
intent не хватило обязательных полей — тогда entrypoint отвечает общим
«не поняла».
"""

from datetime import time
from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import WateringZone
from bot.handlers.watering import ask_interval, ask_notify_time
from bot.keyboards.inline import confirm_delete_keyboard
from bot.keyboards.watering import zone_card_keyboard, zones_menu_keyboard
from bot.services import watering_service
from bot.services.watering_service import MAX_INTERVAL_DAYS, MAX_NAME_LENGTH, MIN_INTERVAL_DAYS, SNOOZE_OPTIONS
from bot.utils.fuzzy import fuzzy_find

from . import router
from .common import reply
from .keyboards import zone_pick_keyboard, zone_result_keyboard
from .states import AIZone

_NOT_FOUND = "⚠️ Зона не найдена, возможно уже удалена."
_INTERVAL_ERROR = f"⚠️ Число дней должно быть от {MIN_INTERVAL_DAYS} до {MAX_INTERVAL_DAYS}."

# Статусы разбора notify_time из intent.
_TIME_MISSING = "missing"  # не названо (null)
_TIME_CLEAR = "clear"      # "" — убрать фиксированное время
_TIME_INVALID = "invalid"  # что-то есть, но это не время
_TIME_SET = "set"


# ---------- Разбор полей intent ----------


def _text(intent: dict, key: str) -> str:
    value = intent.get(key)
    return value.strip() if isinstance(value, str) else ""


def _read_days(value: object) -> tuple[int | None, bool]:
    """(дни, корректно_ли). Не названо (None / "") -> (None, True): вызывающий
    решает, что делать без значения. Названо, но не число или вне
    1..365 -> (None, False)."""
    if value is None or value == "":
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        days = value
    elif isinstance(value, str):
        parsed = watering_service.parse_interval(value)
        if parsed is None:
            return None, False
        days = parsed
    else:
        return None, False
    if not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS:
        return None, False
    return days, True


def _read_notify_time(value: object) -> tuple[time | None, str]:
    """(время_в_UTC, статус). Время от модели — локальное (как на кнопках),
    в UTC его переводим здесь."""
    if value is None:
        return None, _TIME_MISSING
    if not isinstance(value, str):
        return None, _TIME_INVALID
    if not value.strip():
        return None, _TIME_CLEAR
    parsed = watering_service.parse_notify_time(value)
    if parsed is None:
        return None, _TIME_INVALID
    return watering_service.from_display_time(parsed), _TIME_SET


# ---------- Поиск зоны и применение действия ----------


async def _reply_not_found(message: Message, query: str, zones: list[WateringZone]) -> None:
    if zones:
        names = ", ".join(f"«{escape(z.name)}»" for z in zones)
        await message.answer(f"Не нашла зону «{escape(query)}». Твои зоны полива: {names}")
    else:
        await message.answer(
            f"Не нашла зону «{escape(query)}» — зон полива пока нет. Добавь через 💧 Полив "
            "или напиши, например: «создай зону Балкон, раз в 5 дней»"
        )


async def _apply(target: Message | CallbackQuery, state: FSMContext, user_id: int, zone_id: int, params: dict) -> None:
    """Выполняет действие params["op"] над зоной. params — простой dict из
    примитивов, чтобы его можно было положить в FSM на время выбора зоны из
    нескольких подходящих."""
    op = params["op"]
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await reply(target, _NOT_FOUND, None)
            return
        name = escape(zone.name)

        if op == "delete":
            await state.set_state(AIZone.confirm_delete)
            await state.update_data(zone_id=zone.id)
            await reply(
                target,
                f"🗑 Удалить зону «{name}»? Напоминания о ней приходить перестанут.",
                confirm_delete_keyboard("aizdelconfirm", "aizcancel", confirm_label="🗑 Да, удалить"),
            )
            return

        if op == "card":
            text = watering_service.render_card(zone, watering_service.utcnow())
            await reply(target, text, zone_card_keyboard(zone.id))
            return

        if op == "water":
            await watering_service.mark_watered(session, zone)
            text = f"✅ Зона «{name}» полита. Следующий полив через {zone.interval_days} дн."
        elif op == "snooze":
            days = params["days"]
            await watering_service.snooze(session, zone, days)
            text = f"⏰ Зона «{name}»: отложено на {days} дн. Напомню позже."
        elif op == "interval":
            days = params["days"]
            await watering_service.set_interval(session, zone, days)
            text = f"✅ Зона «{name}»: теперь поливаем раз в {days} дн. Отсчёт до следующего полива начался заново."
        elif op == "time":
            hhmm = params["notify_time"]  # "HHMM" в UTC или None — убрать фиксированный час
            notify_time = time(int(hhmm[:2]), int(hhmm[2:])) if hhmm else None
            await watering_service.set_notify_time(session, zone, notify_time)
            if notify_time is None:
                text = f"✅ Зона «{name}»: фиксированный час напоминания убран."
            else:
                text = f"✅ Зона «{name}»: теперь напоминаю {watering_service.describe_notify_time(notify_time)}."
        elif op == "rename":
            new_name = params["new_name"]
            old_name = name
            try:
                await watering_service.rename(session, zone, new_name)
            except watering_service.ZoneAlreadyExists:
                await reply(target, f"⚠️ Зона «{escape(new_name)}» уже есть. Придумай другое название.", None)
                return
            except ValueError:
                await reply(target, f"⚠️ Название должно быть от 1 до {MAX_NAME_LENGTH} символов.", None)
                return
            text = f"✏️ «{old_name}» → «{escape(zone.name)}»"
        else:  # pragma: no cover — защита от опечатки в op
            raise ValueError(f"unknown zone op: {op}")

    await reply(target, text, zone_result_keyboard(zone_id).as_markup())


async def _run_on_zone(
    message: Message, state: FSMContext, user_id: int, zones: list[WateringZone], query: str, params: dict
) -> bool:
    """Находит зону по названию и применяет к ней params. Совпадений
    несколько -> список на выбор, действие применится после выбора."""
    if not query:
        return False
    matches = fuzzy_find(zones, query)
    if not matches:
        await _reply_not_found(message, query, zones)
        return True
    if len(matches) == 1:
        await _apply(message, state, user_id, matches[0].id, params)
        return True
    await state.set_state(AIZone.pick_zone)
    await state.update_data(params=params)
    await message.answer(
        f"Нашла несколько зон «{escape(query)}» — какая?", reply_markup=zone_pick_keyboard(matches).as_markup()
    )
    return True


# ---------- Точки входа из entrypoint ----------


async def handle_zone_add_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    name = _text(intent, "zone_name")
    if not name:
        return False
    if len(name) > MAX_NAME_LENGTH:
        await message.answer(f"⚠️ Название зоны должно быть от 1 до {MAX_NAME_LENGTH} символов.")
        return True
    existing = next((z for z in zones if z.name.strip().lower() == name.lower()), None)
    if existing is not None:
        await message.answer(f"💧 Зона «{escape(existing.name)}» уже есть — ничего не меняла")
        return True

    days, days_ok = _read_days(intent.get("interval_days"))
    if not days_ok:
        await message.answer(_INTERVAL_ERROR)
        return True
    notify_time, time_status = _read_notify_time(intent.get("notify_time"))

    # Чего не хватает — то спрашиваем обычным диалогом создания зоны, теми
    # же кнопками, что и в меню «💧 Полив».
    if days is None:
        await ask_interval(message, state, name)
        return True
    if time_status != _TIME_SET:
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
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), {"op": "water"})


async def handle_zone_snooze_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    days, ok = _read_days(intent.get("snooze_days"))
    if not ok:
        await message.answer(_INTERVAL_ERROR)
        return True
    # Срок не назван — откладываем на самый короткий вариант из кнопок
    # под напоминанием; итоговый срок виден в ответе.
    params = {"op": "snooze", "days": days or SNOOZE_OPTIONS[0]}
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), params)


async def handle_zone_interval_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    days, ok = _read_days(intent.get("interval_days"))
    if not ok:
        await message.answer(_INTERVAL_ERROR)
        return True
    if days is None:
        return False
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), {"op": "interval", "days": days})


async def handle_zone_time_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    notify_time, status = _read_notify_time(intent.get("notify_time"))
    if status == _TIME_MISSING:
        return False
    if status == _TIME_INVALID:
        await message.answer("⚠️ Не поняла время. Напиши в формате ЧЧ:ММ, например: 09:30")
        return True
    hhmm = notify_time.strftime("%H%M") if notify_time else None
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), {"op": "time", "notify_time": hhmm})


async def handle_zone_rename_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    new_name = _text(intent, "new_name")
    if not new_name:
        return False
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), {"op": "rename", "new_name": new_name})


async def handle_zone_delete_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    return await _run_on_zone(message, state, user_id, zones, _text(intent, "zone_name"), {"op": "delete"})


async def handle_zone_list_intent(
    message: Message, state: FSMContext, user_id: int, intent: dict, zones: list[WateringZone]
) -> bool:
    """Без названия — общий обзор зон (как в меню «💧 Полив»), с названием —
    карточка одной зоны."""
    query = _text(intent, "zone_name")
    if query:
        return await _run_on_zone(message, state, user_id, zones, query, {"op": "card"})
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
    await _apply(callback, state, user_id, zone_id, data["params"])


@router.callback_query(StateFilter(AIZone.confirm_delete), F.data == "aizdelconfirm")
async def ai_zone_delete_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        zone = await crud.get_zone(session, data.get("zone_id"), user_id)
        if zone is None:
            await callback.message.edit_text(_NOT_FOUND)
            return
        name = escape(zone.name)
        await watering_service.remove(session, zone)
    await callback.message.edit_text(f"🗑 Удалила зону «{name}»")


@router.callback_query(StateFilter(AIZone.pick_zone, AIZone.confirm_delete), F.data == "aizcancel")
async def ai_zone_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
