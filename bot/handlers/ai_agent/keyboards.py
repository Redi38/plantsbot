"""Клавиатуры, используемые в сценариях ИИ-агента (add_flow / delete_flow / zone_flow)."""

from aiogram.utils.keyboard import InlineKeyboardBuilder


def confirm_group_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Добавить", callback_data="aiconfirmadd", style="success")
    builder.button(text="📁 Другая группа", callback_data="aiothergroup", style="primary")
    builder.button(text="❌ Отменить", callback_data="aicancel", style="danger")
    builder.adjust(2, 1)
    return builder


def duplicate_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Всё равно добавить", callback_data="aiaddforce", style="primary")
    builder.button(text="❌ Отмена", callback_data="aicancel", style="danger")
    builder.adjust(1)
    return builder


def delete_pick_label(plant, group_name_by_id: dict[int, str], multi_group: bool, index: int) -> str:
    """Подпись кнопки для выбора конкретного совпадения при удалении.
    Если совпадения лежат в разных группах — показываем группу (это и
    отличает их друг от друга), иначе группа у всех одна и не помогает
    выбрать, так что показываем комментарий, а если и его нет —
    порядковый номер, чтобы кнопки не были неотличимы."""
    if multi_group:
        group_label = group_name_by_id.get(plant.group_id, "Без группы")
        return f"{plant.name} — {group_label}"
    if plant.comment:
        return f"{plant.name} ({plant.comment})"
    return f"{plant.name} #{index}"


def plant_pick_keyboard(
    matches: list,
    group_name_by_id: dict[int, str],
    multi_group: bool,
    *,
    item_prefix: str,
    cancel_data: str,
    item_style: str = "danger",
) -> InlineKeyboardBuilder:
    """Общая клавиатура выбора растения из нескольких совпадений — для
    удаления (item_prefix="aidelpick", style="danger") и для изменения
    (item_prefix="aieditpick", style="primary"). Раньше это были два
    отдельных, но идентичных по структуре builder'а, различавшихся только
    префиксом и цветом."""
    builder = InlineKeyboardBuilder()
    for i, plant in enumerate(matches, start=1):
        label = delete_pick_label(plant, group_name_by_id, multi_group, i)
        builder.button(text=label, callback_data=f"{item_prefix}:{plant.id}", style=item_style)
    builder.button(text="❌ Отмена", callback_data=cancel_data, style="danger")
    builder.adjust(1)
    return builder


def zone_pick_keyboard(zones: list) -> InlineKeyboardBuilder:
    """Выбор зоны полива, когда под название подошло несколько."""
    builder = InlineKeyboardBuilder()
    for zone in zones:
        builder.button(text=f"💧 {zone.name}", callback_data=f"aizpick:{zone.id}", style="primary")
    builder.button(text="❌ Отмена", callback_data="aizcancel", style="danger")
    builder.adjust(1)
    return builder


def zone_result_keyboard(zone_id: int) -> InlineKeyboardBuilder:
    """Кнопки под результатом действия с зоной: открыть её карточку
    (обычный хендлер wz:{id} из bot/handlers/watering.py) или закрыть."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💧 Открыть зону", callback_data=f"wz:{zone_id}", style="primary")
    builder.button(text="⬅️ Назад", callback_data="closemsg", style="primary")
    builder.adjust(1)
    return builder
