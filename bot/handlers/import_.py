from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.keyboards.inline import confirm_keyboard
from bot.keyboards.reply import BTN_IMPORT, MENU_BUTTONS
from bot.services import import_service
from bot.utils.chat import begin_dialog
from bot.utils.text import split_long_text

router = Router(name="import_")

_pending_imports: dict[int, list[import_service.PreviewGroup]] = {}


class ImportFlow(StatesGroup):
    waiting_input = State()


@router.message(F.text == BTN_IMPORT)
async def cmd_import(message: Message, state: FSMContext) -> None:
    # Как и "Список" — прерывает и сразу подчищает зависшую подсказку
    # добавления/редактирования, если импорт запущен посреди неё.
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=old_msg_id)
        except TelegramBadRequest:
            pass
    await state.set_state(ImportFlow.waiting_input)
    await message.answer(
        "Пришли список растений одним из способов:\n\n"
        "1️⃣ CSV-файл с колонками <code>group,name,comment</code>\n\n"
        "2️⃣ Текст в формате:\n"
        "<code>Алоказии:\n"
        "- Алоказия Полли: пересадила в марте\n"
        "- Алоказия Одора\n\n"
        "Суккуленты:\n"
        "- Хавортия</code>\n\n"
        "Можешь попросить ChatGPT сформировать список именно в таком виде."
    )


@router.message(StateFilter(ImportFlow.waiting_input), F.document)
async def import_file(message: Message, state: FSMContext, bot) -> None:
    file = await bot.get_file(message.document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    raw_text = file_bytes.read().decode("utf-8")

    is_csv = message.document.file_name.lower().endswith(".csv")
    await _process_import(message, state, raw_text, is_csv)


@router.message(StateFilter(ImportFlow.waiting_input), F.text, ~F.text.in_(MENU_BUTTONS))
async def import_text(message: Message, state: FSMContext) -> None:
    await _process_import(message, state, message.text, is_csv=False)


async def _process_import(message: Message, state: FSMContext, raw_text: str, is_csv: bool) -> None:
    try:
        rows = import_service.parse_csv(raw_text) if is_csv else import_service.parse_markdown(raw_text)
    except import_service.ImportParseError as e:
        await message.answer(f"⚠️ {e}\n\nПопробуй ещё раз или /cancel_import")
        return

    async with get_session() as session:
        user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        preview = await import_service.build_preview(session, user.id, rows)

    _pending_imports[message.from_user.id] = preview
    await state.clear()

    preview_text = import_service.render_preview_text(preview)
    chunks = split_long_text(preview_text)

    for chunk in chunks[:-1]:
        await message.answer(chunk)

    await message.answer(
        chunks[-1],
        reply_markup=confirm_keyboard(yes_data="import_confirm", no_data="import_cancel"),
    )


@router.message(Command("cancel_import"), StateFilter(ImportFlow.waiting_input))
async def cancel_import_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Импорт отменён.")


@router.callback_query(F.data == "import_confirm")
async def import_confirm(callback: CallbackQuery) -> None:
    preview = _pending_imports.pop(callback.from_user.id, None)
    if preview is None:
        await callback.answer("Предпросмотр устарел, начни заново кнопкой 📥 Импорт", show_alert=True)
        return

    async with get_session() as session:
        user = await crud.get_or_create_user(session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
        count, skipped = await import_service.commit_import(session, user.id, preview)

    await callback.answer()
    text = f"✅ Импортировано растений: {count}"
    if skipped:
        text += f"\n⚠️ Пропущено как повтор: {skipped}"
    await callback.message.edit_text(text)


@router.callback_query(F.data == "import_cancel")
async def import_cancel(callback: CallbackQuery) -> None:
    _pending_imports.pop(callback.from_user.id, None)
    await callback.answer()
    await callback.message.edit_text("Импорт отменён.")
