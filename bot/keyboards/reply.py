from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_LIST = "📋 Список"
BTN_ADD = "➕ Добавить"
BTN_DELETE = "🗑 Удалить"
BTN_RENAME_GROUP = "✏️ Переименовать группу"
BTN_IMPORT = "📥 Импорт"
BTN_HELP = "ℹ️ Помощь"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Статичное главное меню (не хранит id — дублирует команды кнопками)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_ADD)],
            [KeyboardButton(text=BTN_DELETE), KeyboardButton(text=BTN_RENAME_GROUP)],
            [KeyboardButton(text=BTN_IMPORT), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )
