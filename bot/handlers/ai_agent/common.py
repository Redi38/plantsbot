"""Мелкие хелперы, общие для нескольких сценариев ИИ-агента."""

from aiogram.types import CallbackQuery, Message


async def reply(reply_target: Message | CallbackQuery, text: str, markup) -> None:
    """И обычное сообщение, и нажатие кнопки обрабатываются одинаково во
    всех сценариях ИИ-агента: если пришли из callback — редактируем
    существующее сообщение, если из свободного текста — отвечаем новым."""
    if isinstance(reply_target, CallbackQuery):
        await reply_target.message.edit_text(text, reply_markup=markup)
    else:
        await reply_target.answer(text, reply_markup=markup)
