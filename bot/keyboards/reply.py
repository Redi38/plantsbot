from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_LIST = "📋 Список"
BTN_ADD = "➕ Добавить"
BTN_IMPORT = "📥 Импорт"

MENU_BUTTONS = {BTN_LIST, BTN_ADD, BTN_IMPORT}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Статичное главное меню (не хранит id — дублирует команды кнопками)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_LIST)],
            [KeyboardButton(text=BTN_IMPORT)],
        ],
        resize_keyboard=True,
    )
