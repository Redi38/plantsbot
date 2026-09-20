"""Поиск зоны по названию (fuzzy) и применение действия к найденной зоне.

Логика самих действий (что делают кнопки) уже лежит в
bot/services/watering_service.py — здесь только вызов тех же функций
сервиса, так что поведение совпадает с ручным меню: интервал считается
от момента действия, «полил» пересчитывает срок от сейчас и т.д."""

from datetime import time as _time
from html import escape

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import WateringZone
from bot.keyboards.inline import confirm_delete_keyboard
from bot.keyboards.watering import zone_card_keyboard
from bot.services import watering_service
from bot.services.watering_service import MAX_NAME_LENGTH
from bot.utils.fuzzy import fuzzy_find

from ..common import reply
from ..keyboards import zone_pick_keyboard, zone_result_keyboard
from ..states import AIZone
from .intent import NOT_FOUND


async def reply_not_found(message: Message, query: str, zones: list[WateringZone]) -> None:
    if zones:
        names = ", ".join(f"«{escape(z.name)}»" for z in zones)
        await message.answer(f"Не нашла зону «{escape(query)}». Твои зоны полива: {names}")
    else:
        await message.answer(
            f"Не нашла зону «{escape(query)}» — зон полива пока нет. Добавь через 💧 Полив "
            "или напиши, например: «создай зону Балкон, раз в 5 дней»"
        )


async def apply(target: Message | CallbackQuery, state: FSMContext, user_id: int, zone_id: int, params: dict) -> None:
    """Выполняет действие params["op"] над зоной. params — простой dict из
    примитивов, чтобы его можно было положить в FSM на время выбора зоны из
    нескольких подходящих."""
    op = params["op"]
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            await reply(target, NOT_FOUND, None)
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
            notify_time = _time(int(hhmm[:2]), int(hhmm[2:])) if hhmm else None
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


async def run_on_zone(
    message: Message, state: FSMContext, user_id: int, zones: list[WateringZone], query: str, params: dict
) -> bool:
    """Находит зону по названию и применяет к ней params. Совпадений
    несколько -> список на выбор, действие применится после выбора."""
    if not query:
        return False
    matches = fuzzy_find(zones, query)
    if not matches:
        await reply_not_found(message, query, zones)
        return True
    if len(matches) == 1:
        await apply(message, state, user_id, matches[0].id, params)
        return True
    await state.set_state(AIZone.pick_zone)
    await state.update_data(params=params)
    await message.answer(
        f"Нашла несколько зон «{escape(query)}» — какая?", reply_markup=zone_pick_keyboard(matches).as_markup()
    )
    return True
