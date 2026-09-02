from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.db import crud
from bot.db.database import get_session
from bot.services import plant_service
from bot.utils.text import split_long_text

router = Router(name="list_view")

HELP_TEXT = (
    "🌿 <b>Бот для учёта растений</b>\n\n"
    "/list — общий список всех растений по группам\n"
    "/add — добавить растение\n"
    "/delete — удалить растение\n"
    "/rename_group — переименовать группу\n"
    "/import — импортировать список (CSV или текст)\n\n"
    "Также можно просто написать своими словами, например:\n"
    "<i>«добавь алоказию полли, полила вчера»</i> — я пойму 🙂"
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    async with get_session() as session:
        await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        await session.commit()
    await message.answer(HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        text = await plant_service.render_tree(session, user.id)

    for chunk in split_long_text(text):
        await message.answer(chunk)
