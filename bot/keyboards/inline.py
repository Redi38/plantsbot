from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Group


def groups_keyboard(
    groups: list[Group],
    prefix: str,
    allow_none: bool = True,
    none_label: str = "Без группы",
    cancel_data: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"{prefix}:{group.id}", style="primary")
    if allow_none:
        builder.button(text=none_label, callback_data=f"{prefix}:none", style="primary")
    builder.button(text="➕ Новая группа", callback_data=f"{prefix}:new", style="primary")
    if cancel_data:
        builder.button(text="❌ Отмена", callback_data=cancel_data, style="danger")
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Импортировать", callback_data=yes_data, style="success")
    builder.button(text="❌ Отмена", callback_data=no_data, style="danger")
    builder.adjust(2)
    return builder.as_markup()
