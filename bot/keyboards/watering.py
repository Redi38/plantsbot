"""Клавиатуры зон полива. Формат callback_data (префикс wz — чтобы не
пересекаться с lg*/add* из остальных модулей):

  wz:{id}            — открыть карточку зоны
  wzmenu             — назад к списку зон
  wzadd / wzcancel   — начать / отменить создание зоны
  wzint:{days}       — интервал при создании зоны
  wztime:{HHMM} / wztskip — время напоминания при создании зоны / без фикс. часа
  wzedit:{id}        — сменить интервал зоны
  wzeint:{id}:{days} — новый интервал существующей зоны
  wztedit:{id}                 — сменить время напоминания зоны
  wzetime:{id}:{HHMM} / wztskip:{id} — новое время зоны / убрать фикс. час
  wzwater:{id}       — «полил» из карточки
  wzdel:{id} / wzdelc:{id} — удалить (запрос / подтверждение)
  wzdone:{id}        — «полил» из напоминания
  wzlate:{id}:{days} — «отложить» из напоминания
"""

from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import WateringZone
from bot.services.watering_service import INTERVAL_PRESETS, NOTIFY_TIME_PRESETS, SNOOZE_OPTIONS, is_due


def zones_menu_keyboard(zones: list[WateringZone], now: datetime) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for zone in zones:
        icon = "🔔" if is_due(zone, now) else "💧"
        builder.button(text=f"{icon} {zone.name}", callback_data=f"wz:{zone.id}", style="primary")
    builder.button(text="➕ Добавить зону", callback_data="wzadd", style="success")
    builder.button(text="⬅️ Назад", callback_data="closemsg", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def zone_card_keyboard(zone_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Полил", callback_data=f"wzwater:{zone_id}", style="success")
    builder.button(text="✏️ Интервал", callback_data=f"wzedit:{zone_id}", style="primary")
    builder.button(text="🕒 Время", callback_data=f"wztedit:{zone_id}", style="primary")
    builder.button(text="🗑 Удалить", callback_data=f"wzdel:{zone_id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data="wzmenu", style="primary")
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


def interval_keyboard(
    prefix: str, back_data: str, *, back_label: str = "❌ Отмена", back_style: str = "danger"
) -> InlineKeyboardMarkup:
    """Быстрый выбор интервала сеткой 3×3. Callback каждой кнопки —
    f"{prefix}:{days}"; префикс различает создание зоны и смену интервала."""
    builder = InlineKeyboardBuilder()
    for days in INTERVAL_PRESETS:
        builder.button(text=f"{days} дн.", callback_data=f"{prefix}:{days}", style="primary")
    builder.button(text=back_label, callback_data=back_data, style=back_style)
    builder.adjust(3, 3, 3, 1)
    return builder.as_markup()


def notify_time_keyboard(
    prefix: str, skip_data: str, back_data: str, *, back_label: str = "❌ Отмена", back_style: str = "danger"
) -> InlineKeyboardMarkup:
    """Быстрый выбор времени напоминания сеткой 3×3 + «без фикс. часа».
    Callback каждой кнопки — f"{prefix}:{HHMM}"; префикс различает создание
    зоны и смену времени существующей."""
    builder = InlineKeyboardBuilder()
    for t in NOTIFY_TIME_PRESETS:
        builder.button(text=t.strftime("%H:%M"), callback_data=f"{prefix}:{t.strftime('%H%M')}", style="primary")
    builder.button(text="🌊 Без фикс. часа", callback_data=skip_data, style="primary")
    builder.button(text=back_label, callback_data=back_data, style=back_style)
    builder.adjust(3, 3, 1, 1)
    return builder.as_markup()


def cancel_keyboard(callback_data: str = "wzcancel") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=callback_data, style="danger")
    return builder.as_markup()


def reminder_keyboard(zone_id: int) -> InlineKeyboardMarkup:
    """Кнопки под напоминанием: «Полил» и ряд «отложить на N дн.»."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Полил", callback_data=f"wzdone:{zone_id}", style="success")
    for days in SNOOZE_OPTIONS:
        builder.button(text=f"⏰ Отложить на {days} дн.", callback_data=f"wzlate:{zone_id}:{days}", style="primary")
    builder.adjust(1)
    return builder.as_markup()
