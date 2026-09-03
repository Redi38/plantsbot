"""Утилита, чтобы бот не заваливал чат новыми сообщениями во время
многошаговых диалогов (добавление, изменение, переименование и т.п.):
каждый следующий шаг удаляет предыдущее сообщение бота и присылает новое
вместо него (само сообщение пользователя не трогаем).

Id "рабочего" сообщения бота хранится в FSMContext (ключ _KEY), поэтому
переживает переходы между состояниями стейт-машины."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

_KEY = "_bot_msg_id"


async def begin_dialog(state: FSMContext) -> int | None:
    """Начинает новый диалог с нуля: возвращает id ранее отслеживаемого
    рабочего сообщения бота (если сценарий уже был начат — из другой
    точки входа или повторным нажатием той же кнопки) и полностью
    очищает состояние. Вызывающий код должен сам удалить или
    переиспользовать (edit_text) это сообщение, чтобы в чате не
    оставалось "осиротевших" подсказок."""
    data = await state.get_data()
    msg_id = data.get(_KEY)
    await state.clear()
    return msg_id


async def render(message: Message, state: FSMContext, text: str, reply_markup=None) -> None:
    """Удаляет предыдущее сообщение бота в этом диалоге (если такое
    отслеживается) и присылает новое вместо него — так в чате не
    накапливается цепочка старых подсказок бота."""
    data = await state.get_data()
    msg_id = data.get(_KEY)

    if msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except TelegramBadRequest:
            pass

    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data(**{_KEY: sent.message_id})


async def track_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Запоминает сообщение под инлайн-клавиатурой (которое callback только
    что отредактировал) как рабочее — чтобы последующий render() в этом же
    диалоге знал, какое сообщение бота удалить перед следующим шагом."""
    await state.update_data(**{_KEY: callback.message.message_id})


async def pop_tracked(state: FSMContext) -> int | None:
    """Достаёт id рабочего сообщения (для финального удаления) и убирает
    его из данных состояния (обычно вызывается перед state.clear())."""
    data = await state.get_data()
    return data.get(_KEY)
