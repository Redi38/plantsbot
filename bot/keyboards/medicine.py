"""Клавиатуры аптечки. Формат callback_data (префикс med — чтобы не
пересекаться с остальными модулями):

  mg:{token}        — таблица препаратов одного типа (token — kind_token) или mg:all — главный экран с общей таблицей
  medpage:{token}:{n} — страница n таблицы (пагинация)
  medpick:{token}   — выбрать препарат для удаления из таблицы token
  med:{id}          — открыть карточку препарата
  medmenu           — назад к меню типов
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
from bot.keyboards.inline import add_pagination_buttons
from bot.services.medicine_service import KIND_PRESETS, KindGroup, button_label


def medicines_menu_keyboard(groups: list[KindGroup], page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Главный экран аптечки: под общей таблицей — пагинация (если страниц
    несколько), кнопка на каждый тип (с числом препаратов), под ними в
    одном ряду «Добавить препарат» и «Удалить», затем «Назад»."""
    builder = InlineKeyboardBuilder()
    row_sizes = []
    pagination_size = add_pagination_buttons(builder, page, total_pages, lambda p: f"medpage:all:{p}")
    if pagination_size:
        row_sizes.append(pagination_size)
    for group in groups:
        builder.button(text=f"{group.name} ({len(group.medicines)})", callback_data=f"mg:{group.token}")
    builder.button(text="➕ Добавить препарат", callback_data="medadd", style="success")
    if groups:
        builder.button(text="🗑 Удалить", callback_data="medpick:all", style="danger")
    builder.button(text="⬅️ Назад", callback_data="closemsg", style="primary")
    # типы — по одной кнопке в ряд, «Добавить» и «Удалить» — на одном уровне под ними
    builder.adjust(*row_sizes, *([1] * len(groups)), 2 if groups else 1, 1)
    return builder.as_markup()


def medicine_pages_keyboard(token: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Кнопки под таблицей типа (или всех препаратов): пагинация, добавить /
    удалить, назад в меню типов."""
    builder = InlineKeyboardBuilder()
    row_sizes = []
    pagination_size = add_pagination_buttons(builder, page, total_pages, lambda p: f"medpage:{token}:{p}")
    if pagination_size:
        row_sizes.append(pagination_size)
    builder.button(text="➕ Добавить", callback_data="medadd", style="success")
    builder.button(text="🗑 Удалить", callback_data=f"medpick:{token}", style="danger")
    builder.button(text="⬅️ Назад", callback_data="medmenu", style="primary")
    builder.adjust(*row_sizes, 2, 1)
    return builder.as_markup()


def medicine_pick_keyboard(medicines: list[Medicine], today: date, token: str) -> InlineKeyboardMarkup:
    """Выбор препарата для удаления: кнопка на препарат, назад — к таблице."""
    builder = InlineKeyboardBuilder()
    for medicine in medicines:
        builder.button(text=button_label(medicine, today), callback_data=f"meddel:{medicine.id}", style="danger")
    builder.button(text="⬅️ Назад", callback_data=f"mg:{token}", style="primary")
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
