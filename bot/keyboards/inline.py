from typing import Callable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Group


def add_pagination_buttons(
    builder: InlineKeyboardBuilder, page: int, total_pages: int, page_callback: Callable[[int], str]
) -> int:
    """Добавляет в builder кнопки пагинации ◀️ {page}/{total} ▶️ с
    зацикливанием (с последней страницы на первую и обратно), если страниц
    больше одной. page_callback(page_number) должен вернуть callback_data
    для перехода на указанную страницу — конкретный формат (какой префикс,
    что ещё зашито в токене) остаётся за вызывающим кодом, здесь общая
    только сама логика prev/next с зацикливанием, которая раньше была
    продублирована почти дословно в plants.py и list_view.py.

    Возвращает число добавленных кнопок (0, если страница одна — тогда
    ничего не добавляется, или 3) — используется вызывающим кодом при
    сборке row_sizes для builder.adjust()."""
    if total_pages <= 1:
        return 0
    prev_page = page - 1 if page > 1 else total_pages
    next_page = page + 1 if page < total_pages else 1
    builder.button(text="◀️", callback_data=page_callback(prev_page), style="primary")
    builder.button(text=f"{page}/{total_pages}", callback_data="list_noop", style="primary")
    builder.button(text="▶️", callback_data=page_callback(next_page), style="primary")
    return 3


def groups_keyboard(
    groups: list[Group],
    prefix: str,
    allow_none: bool = True,
    none_label: str = "Без группы",
    cancel_data: str | None = None,
    new_label: str = "➕ Новая группа",
    allow_new: bool = True,
    back_data: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"{prefix}:{group.id}", style="primary")
    if allow_none:
        builder.button(text=none_label, callback_data=f"{prefix}:none", style="primary")
    if allow_new:
        builder.button(text=new_label, callback_data=f"{prefix}:new", style="primary")
    if back_data:
        builder.button(text="⬅️ Назад", callback_data=back_data, style="danger")
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


def confirm_delete_keyboard(
    confirm_data: str,
    cancel_data: str,
    *,
    confirm_label: str = "🗑 Удалить",
    cancel_label: str = "❌ Отмена",
    cancel_style: str = "danger",
) -> InlineKeyboardMarkup:
    """Общая клавиатура подтверждения удаления — раньше три независимых
    сценария (удаление растения через ИИ-агента, удаление растения из
    просмотра списка, удаление группы целиком) строили один и тот же по
    сути билдер (кнопка danger-удаления сверху + кнопка отмены/назад
    снизу) каждый в своём модуле со своими подписями/callback_data.
    Здесь — одна параметризуемая версия под все три случая."""
    builder = InlineKeyboardBuilder()
    builder.button(text=confirm_label, callback_data=confirm_data, style="danger")
    builder.button(text=cancel_label, callback_data=cancel_data, style=cancel_style)
    builder.adjust(1)
    return builder.as_markup()
