from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import get_session
from bot.keyboards.inline import confirm_keyboard
from bot.keyboards.reply import BTN_IMPORT, MENU_BUTTONS
from bot.services import import_service
from bot.utils.chat import begin_dialog, safe_delete_message
from bot.utils.text import split_long_text

router = Router(name="import_")

_pending_imports: dict[int, list[import_service.PreviewGroup]] = {}


class ImportFlow(StatesGroup):
    waiting_input = State()


@router.message(F.text == BTN_IMPORT)
async def cmd_import(message: Message, state: FSMContext) -> None:
    old_msg_id = await begin_dialog(state)
    if old_msg_id:
        await safe_delete_message(message.bot, message.chat.id, old_msg_id)
    await state.set_state(ImportFlow.waiting_input)

    export_builder = InlineKeyboardBuilder()
    export_builder.button(text="📤 Экспорт", callback_data="export_plants", style="primary")

    await message.answer(
        "📥 Пришли список растений одним из способов:\n\n"
        "1️⃣ CSV-файл с колонками <code>group,name,comment</code>\n\n"
        "2️⃣ Текст в формате:\n"
        "<code>Алоказии:\n"
        "- Алоказия Полли: пересадила в марте\n"
        "- Алоказия Одора\n\n"
        "Суккуленты:\n"
        "- Хавортия</code>\n\n"
        "Можешь попросить ChatGPT сформировать список именно в таком виде.\n\n"
        "Или выгрузи свои текущие растения в CSV кнопкой ниже 👇",
        reply_markup=export_builder.as_markup(),
    )


@router.message(StateFilter(ImportFlow.waiting_input), F.document)
async def import_file(message: Message, state: FSMContext, bot, user_id: int) -> None:
    file = await bot.get_file(message.document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    raw_text = file_bytes.read().decode("utf-8")

    is_csv = message.document.file_name.lower().endswith(".csv")
    await _process_import(message, state, user_id, raw_text, is_csv)


@router.message(StateFilter(ImportFlow.waiting_input), F.text, ~F.text.in_(MENU_BUTTONS))
async def import_text(message: Message, state: FSMContext, user_id: int) -> None:
    await _process_import(message, state, user_id, message.text, is_csv=False)


async def _process_import(message: Message, state: FSMContext, user_id: int, raw_text: str, is_csv: bool) -> None:
    try:
        rows = import_service.parse_csv(raw_text) if is_csv else import_service.parse_markdown(raw_text)
    except import_service.ImportParseError as e:
        await message.answer(f"⚠️ {e}\n\nПопробуй ещё раз или /cancel_import")
        return

    async with get_session() as session:
        preview = await import_service.build_preview(session, user_id, rows)

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


@router.callback_query(F.data == "export_plants")
async def export_plants(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        csv_text = await import_service.export_to_csv(session, user_id)

    if csv_text.count("\n") <= 1:
        await callback.answer("У тебя пока нет растений для экспорта", show_alert=True)
        return

    file = BufferedInputFile(
        csv_text.encode("utf-8-sig"),
        filename=f"plants_{datetime.now(timezone.utc):%Y-%m-%d}.csv",
    )
    await callback.answer()
    await state.clear()
    await safe_delete_message(callback.bot, callback.message.chat.id, callback.message.message_id)
    await callback.bot.send_document(callback.message.chat.id, file)


@router.message(Command("cancel_import"), StateFilter(ImportFlow.waiting_input))
async def cancel_import_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Импорт отменён")


@router.callback_query(F.data == "import_confirm")
async def import_confirm(callback: CallbackQuery, user_id: int) -> None:
    preview = _pending_imports.pop(callback.from_user.id, None)
    if preview is None:
        await callback.answer("Предпросмотр устарел, начни заново кнопкой 📥 Импорт", show_alert=True)
        return

    async with get_session() as session:
        count, skipped = await import_service.commit_import(session, user_id, preview)

    await callback.answer()
    text = f"✅ Импортировано растений: {count}"
    if skipped:
        text += f"\n⚠️ Пропущено как повтор: {skipped}"
    await callback.message.edit_text(text)


@router.callback_query(F.data == "import_cancel")
async def import_cancel(callback: CallbackQuery) -> None:
    _pending_imports.pop(callback.from_user.id, None)
    await callback.answer("❌ Импорт отменён")
    await callback.message.delete()
