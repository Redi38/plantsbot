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


async def safe_delete_message(bot, chat_id: int, message_id: int) -> None:
    """Удаляет сообщение бота, молча игнорируя ошибку, если оно уже не
    существует (удалено раньше, слишком старое для Telegram API и т.п.) —
    этот try/except повторялся почти дословно во всех хендлерах, где нужно
    подчистить предыдущий шаг диалога."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def safe_edit_text(message: Message, text: str, reply_markup=None) -> None:
    """edit_text с тем же молчаливым проглатыванием TelegramBadRequest —
    типично когда пользователь успел нажать другую кнопку/сообщение уже
    неактуально. Тот же паттерн, что и safe_delete_message, но для правки
    текста вместо удаления."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass


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
        await safe_delete_message(message.bot, message.chat.id, msg_id)

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
