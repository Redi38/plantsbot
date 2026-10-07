from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_LIST = "📋 Список"
BTN_ADD = "➕ Добавить"
BTN_IMPORT = "📥 Импорт"
BTN_WATER = "💧 Полив"
BTN_MEDS = "🧪 Аптечка"

MENU_BUTTONS = {BTN_LIST, BTN_ADD, BTN_IMPORT, BTN_WATER, BTN_MEDS}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Статичное главное меню (не хранит id — дублирует команды кнопками)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_LIST)],
            [KeyboardButton(text=BTN_IMPORT), KeyboardButton(text=BTN_WATER)],
            [KeyboardButton(text=BTN_MEDS)],
        ],
        resize_keyboard=True,
    )
