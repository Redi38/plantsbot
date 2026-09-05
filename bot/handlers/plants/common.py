from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.keyboards.inline import add_pagination_buttons

PLANT_PICK_PAGE_SIZE = 30


async def plants_for_token(session, user_id: int, token: str) -> list:
    """Список растений, отображаемых в конкретном списке (группа / без
    группы / всё) — используется для меню выбора растения в изменении и
    удалении, вызванных из этого списка."""
    if token == "all":
        groups, ungrouped = await crud.get_full_tree(session, user_id)
        plants = list(ungrouped)
        for g in groups:
            plants.extend(g.plants)
        return plants
    if token == "none":
        _, ungrouped = await crud.get_full_tree(session, user_id)
        return list(ungrouped)
    group = await crud.get_group(session, int(token), user_id)
    return list(group.plants) if group else []


def paginate(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    """Режет items на страницу нужного размера. page зажимается в
    допустимый диапазон [1, total_pages] — так безопаснее, чем падать
    на некорректном номере страницы из старого/чужого callback_data."""
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start : start + page_size], page, total_pages


def plant_pick_keyboard(
    item_prefix: str,
    page_prefix: str,
    token: str,
    items_page: list,
    page: int,
    total_pages: int,
    back_data: str,
):
    """Клавиатура выбора растения из списка (для удаления/изменения) —
    список растений на текущей странице, под ним пагинация (если страниц
    больше одной), а под пагинацией — "Назад" и "Отмена". При 200+
    растениях без пагинации клавиатура получалась настолько большой, что
    Telegram не мог её нормально отрисовать — растения просто не
    отображались."""
    builder = InlineKeyboardBuilder()
    for plant in items_page:
        builder.button(text=plant.name, callback_data=f"{item_prefix}:{token}:{plant.id}", style="danger")
    row_sizes = [1] * len(items_page)

    pagination_count = add_pagination_buttons(builder, page, total_pages, lambda p: f"{page_prefix}:{token}:{p}")
    if pagination_count:
        row_sizes.append(pagination_count)

    builder.button(text="⬅️ Назад", callback_data=back_data, style="primary")
    row_sizes.append(2)

    builder.adjust(*row_sizes)
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="addcancel", style="danger")
    return builder
