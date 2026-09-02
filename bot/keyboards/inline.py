from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Group


def groups_keyboard(
    groups: list[Group], prefix: str, allow_none: bool = True, none_label: str = "Без группы"
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"{prefix}:{group.id}")
    if allow_none:
        builder.button(text=none_label, callback_data=f"{prefix}:none")
    builder.button(text="➕ Новая группа", callback_data=f"{prefix}:new")
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=yes_data)
    builder.button(text="❌ Отмена", callback_data=no_data)
    builder.adjust(2)
    return builder.as_markup()


def plant_delete_keyboard(plant_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"plant_delete_confirm:{plant_id}"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )
