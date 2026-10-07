"""Клавиатуры аптечки. Формат callback_data (префикс med — чтобы не
пересекаться с остальными модулями):

  med:{id}          — открыть карточку препарата
  medmenu           — назад к списку препаратов
  medadd / medcancel — начать / отменить добавление
  medkind:{i}       — выбор типа из KIND_PRESETS при добавлении
  medskip           — пропустить необязательный шаг (вещество / срок / комментарий)
  meddel:{id} / meddelc:{id} — удалить (запрос / подтверждение)
  medtrash:{id}     — «выбросил» под напоминанием: убрать препарат из аптечки
  medok             — «понятно» под напоминанием: просто убрать сообщение
"""

from datetime import date

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Medicine
from bot.services.medicine_service import KIND_PRESETS, button_label


def medicines_menu_keyboard(medicines: list[Medicine], today: date) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить препарат", callback_data="medadd", style="success")
    for medicine in medicines:
        builder.button(text=button_label(medicine, today), callback_data=f"med:{medicine.id}", style="primary")
    builder.button(text="⬅️ Назад", callback_data="closemsg", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def medicine_card_keyboard(medicine_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"meddel:{medicine_id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data="medmenu", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def kind_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, kind in enumerate(KIND_PRESETS):
        builder.button(text=kind, callback_data=f"medkind:{index}", style="primary")
    builder.button(text="❌ Отмена", callback_data="medcancel", style="danger")
    builder.adjust(2)
    return builder.as_markup()


def skip_keyboard() -> InlineKeyboardMarkup:
    """Шаг можно пропустить кнопкой или отменить весь диалог."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data="medskip", style="primary")
    builder.button(text="❌ Отмена", callback_data="medcancel", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def reminder_keyboard(medicine_id: int) -> InlineKeyboardMarkup:
    """Кнопки под напоминанием: убрать препарат из аптечки или оставить как есть."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Выбросил — убрать из аптечки", callback_data=f"medtrash:{medicine_id}", style="danger")
    builder.button(text="👌 Понятно", callback_data="medok", style="primary")
    builder.adjust(1)
    return builder.as_markup()
