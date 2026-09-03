from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group
from bot.keyboards.reply import BTN_LIST, main_menu_keyboard
from bot.services import plant_service
from bot.utils.chat import begin_dialog

router = Router(name="list_view")

HELP_TEXT = (
    "🌿 <b>Бот для учёта растений</b>\n\n"
    "Пользуйся кнопками внизу экрана 👇\n\n"
    "📋 Список – список растений по группам (там же можно добавить, изменить, удалить, переименовать группу)\n"
    "➕ Добавить – добавить растение\n"
    "📥 Импорт – импортировать список (CSV или текст)\n\n"
    "Команды: /start – начало и справка\n\n"
    "Также можно просто написать своими словами, например:\n"
    "<i>«добавь алоказию полли, полила вчера»</i> – я пойму 🙂"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard())


# ---------- Меню групп (динамическое, по числу групп пользователя) ----------

def _group_menu_keyboard(groups: list[Group], ungrouped_label: str | None):
    """Строится заново под конкретного пользователя: одна кнопка на группу
    (id зашит в callback_data, поэтому переименование/дубли названий не мешают)."""
    builder = InlineKeyboardBuilder()
    total = 0
    for group in groups:
        count = len(group.plants)
        total += count
        builder.button(text=f"{group.name} ({count})", callback_data=f"lg:{group.id}")
    if ungrouped_label:
        count = len(ungrouped_label[1])
        total += count
        builder.button(text=f"{ungrouped_label[0]} ({count})", callback_data="lg:none")
    builder.button(text=f"📋 Показать всё ({total})", callback_data="lg:all", style="primary")
    builder.adjust(1)
    return builder.as_markup()


async def _group_menu_text_and_kb(user_id: int):
    async with get_session() as session:
        groups, ungrouped = await crud.get_full_tree(session, user_id)
        if not groups and not ungrouped:
            return "Пока нет ни одного растения. Добавь первое кнопкой ➕ Добавить", None
        ungrouped_label = (await plant_service.get_ungrouped_label(session, user_id), ungrouped) if ungrouped else None
    return "🌿 Выбери группу:", _group_menu_keyboard(groups, ungrouped_label)


@router.message(F.text == BTN_LIST)
async def cmd_list(message: Message, state: FSMContext, user_id: int) -> None:
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=old_msg_id)
        except TelegramBadRequest:
            pass
    text, kb = await _group_menu_text_and_kb(user_id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "lgmenu")
async def list_menu_back(callback: CallbackQuery, user_id: int) -> None:
    text, kb = await _group_menu_text_and_kb(user_id)
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass


# ---------- Просмотр конкретной группы (или "всё") с пагинацией ----------

async def pages_for(user_id: int, token: str) -> tuple[str, list[str]] | None:
    async with get_session() as session:
        if token == "all":
            return "Все растения", await plant_service.render_pages(session, user_id)
        if token == "none":
            return await plant_service.render_group_pages(session, user_id, None)
        return await plant_service.render_group_pages(session, user_id, int(token))


def group_pages_keyboard(token: str, page: int, total_pages: int):
    builder = InlineKeyboardBuilder()

    pagination_row_size = 0

    def add_pagination():
        nonlocal pagination_row_size
        if total_pages > 1:
            prev_page = page - 1 if page > 1 else total_pages
            builder.button(text="◀️", callback_data=f"lgpage:{token}:{prev_page}", style="primary")
            pagination_row_size += 1
            builder.button(text=f"{page}/{total_pages}", callback_data="list_noop", style="primary")
            pagination_row_size += 1
            next_page = page + 1 if page < total_pages else 1
            builder.button(text="▶️", callback_data=f"lgpage:{token}:{next_page}", style="primary")
            pagination_row_size += 1

    def add_crud():
        builder.button(text="➕ Добавить", callback_data=f"lgadd:{token}", style="success")
        builder.button(text="✏️ Изменить", callback_data=f"lgedit:{token}", style="primary")
        builder.button(text="🗑 Удалить", callback_data=f"lgdel:{token}", style="danger")

    def add_rename_group():
        builder.button(text="✏️ Переименовать группу", callback_data=f"lgrename:{token}", style="primary")

    def add_back():
        builder.button(text="⬅️ Назад", callback_data="lgmenu", style="primary")

    is_real_group = token not in ("all", "none")

    if token == "all":
        add_crud()
        add_pagination()
        add_back()
        row_sizes = [3] + ([pagination_row_size] if pagination_row_size else []) + [1]
    else:
        if total_pages > 1:
            add_pagination()
            add_crud()
            row_sizes = [pagination_row_size, 3]
        else:
            add_crud()
            row_sizes = [3]

        if is_real_group:
            add_rename_group()
            row_sizes.append(1)

        add_back()
        row_sizes.append(1)

    builder.adjust(*row_sizes)
    return builder.as_markup()


async def show_group_page(callback: CallbackQuery, user_id: int, token: str, page: int) -> None:
    result = await pages_for(user_id, token)
    await callback.answer()
    if result is None:
        await callback.message.edit_text("⚠️ Группа не найдена, возможно уже удалена.")
        return
    _, pages = result
    page = max(1, min(page, len(pages)))
    try:
        await callback.message.edit_text(pages[page - 1], reply_markup=group_pages_keyboard(token, page, len(pages)))
    except TelegramBadRequest:
        pass


async def send_group_page(
    message: Message,
    user_id: int,
    token: str,
    page: int = 1,
    edit_message_id: int | None = None,
    notice: str | None = None,
) -> None:
    result = await pages_for(user_id, token)
    if result is None:
        return
    _, pages = result
    page = max(1, min(page, len(pages)))
    text = pages[page - 1]
    if notice:
        text = f"{notice}\n\n{text}"
    markup = group_pages_keyboard(token, page, len(pages))

    if edit_message_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=edit_message_id)
        except TelegramBadRequest:
            pass

    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("lg:"))
async def list_group_open(callback: CallbackQuery, user_id: int) -> None:
    token = callback.data.split(":", 1)[1]
    await show_group_page(callback, user_id, token, 1)


@router.callback_query(F.data.startswith("lgpage:"))
async def list_group_page(callback: CallbackQuery, user_id: int) -> None:
    _, token, page = callback.data.split(":", 2)
    await show_group_page(callback, user_id, token, int(page))


@router.callback_query(F.data == "list_noop")
async def list_noop(callback: CallbackQuery) -> None:
    await callback.answer()
