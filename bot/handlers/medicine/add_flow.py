"""Сценарий добавления препарата (FSM MedAdd):

  название (обязательно) -> тип (обязательно) -> действующее вещество ->
  срок годности -> комментарий

Три последних шага необязательные: их можно пропустить кнопкой «Пропустить».
Данные между шагами лежат в FSM; срок годности — строкой ISO, чтобы не
зависеть от того, как хранилище состояний сериализует даты.
"""

from datetime import date
from html import escape

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db.database import get_session
from bot.keyboards.inline import cancel_keyboard
from bot.keyboards.medicine import kind_keyboard, skip_keyboard
from bot.keyboards.reply import MENU_BUTTONS
from bot.services import medicine_service
from bot.services.medicine_service import (
    MAX_COMMENT_LENGTH,
    MAX_KIND_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SUBSTANCE_LENGTH,
    NOTIFY_DAYS_BEFORE,
)
from bot.utils.chat import (
    begin_dialog,
    delete_user_message,
    pop_tracked,
    render,
    safe_delete_message,
    safe_edit_text,
    track_callback,
)

from . import router
from .common import leave_dialog, menu_view
from .states import MedAdd

_CANCEL = cancel_keyboard("medcancel")


# ---------- Старт / отмена ----------


@router.callback_query(F.data == "medadd")
async def add_start(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    old_msg_id = await begin_dialog(state)
    if old_msg_id and old_msg_id != callback.message.message_id:
        await safe_delete_message(callback.bot, callback.message.chat.id, old_msg_id)
    await state.set_state(MedAdd.name)
    await callback.message.edit_text(
        "🧪 Как называется препарат для обработки растений?\n\nНапример: «Актара», «Фитоверм», «Топаз»",
        reply_markup=_CANCEL,
    )
    await track_callback(callback, state)


@router.callback_query(F.data == "medcancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer("Отменено")
    await leave_dialog(state)
    text, kb = await menu_view(user_id)
    await safe_edit_text(callback.message, text, reply_markup=kb)


# ---------- 1. Название ----------


@router.message(StateFilter(MedAdd.name), F.text, ~F.text.in_(MENU_BUTTONS))
async def add_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    await delete_user_message(message)
    if not name or len(name) > MAX_NAME_LENGTH:
        await render(
            message, state, f"⚠️ Название должно быть от 1 до {MAX_NAME_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=_CANCEL,
        )
        return
    await state.update_data(name=name)
    await _ask_kind(message, state)


# ---------- 2. Тип ----------


async def _ask_kind(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(MedAdd.kind)
    await render(
        message,
        state,
        f"📦 Какой тип у препарата «{escape(data['name'])}»?\n\nВыбери кнопкой или напиши свой вариант.",
        reply_markup=kind_keyboard(),
    )


@router.callback_query(StateFilter(MedAdd.kind), F.data.regexp(r"^medkind:\d+$"))
async def add_kind_button(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":", 1)[1])
    if index >= len(medicine_service.KIND_PRESETS):
        await callback.answer()
        return
    await callback.answer()
    await state.update_data(kind=medicine_service.KIND_PRESETS[index])
    await _ask_substance(callback.message, state, edit=True)


@router.message(StateFilter(MedAdd.kind), F.text, ~F.text.in_(MENU_BUTTONS))
async def add_kind_text(message: Message, state: FSMContext) -> None:
    kind = message.text.strip()
    await delete_user_message(message)
    if not kind or len(kind) > MAX_KIND_LENGTH:
        await render(
            message, state, f"⚠️ Тип должен быть от 1 до {MAX_KIND_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=kind_keyboard(),
        )
        return
    await state.update_data(kind=kind)
    await _ask_substance(message, state, edit=False)


# ---------- 3. Действующее вещество (необязательно) ----------


async def _ask_substance(message: Message, state: FSMContext, *, edit: bool) -> None:
    await state.set_state(MedAdd.substance)
    text = "⚗️ Действующее вещество (необязательно)\n\nНапример: «тиаметоксам». Можно пропустить."
    if edit:
        await safe_edit_text(message, text, reply_markup=skip_keyboard())
    else:
        await render(message, state, text, reply_markup=skip_keyboard())


@router.callback_query(StateFilter(MedAdd.substance), F.data == "medskip")
async def add_substance_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(active_substance=None)
    await _ask_expires(callback.message, state, edit=True)


@router.message(StateFilter(MedAdd.substance), F.text, ~F.text.in_(MENU_BUTTONS))
async def add_substance_text(message: Message, state: FSMContext) -> None:
    substance = message.text.strip()
    await delete_user_message(message)
    if len(substance) > MAX_SUBSTANCE_LENGTH:
        await render(
            message, state, f"⚠️ Слишком длинно — не больше {MAX_SUBSTANCE_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=skip_keyboard(),
        )
        return
    await state.update_data(active_substance=substance or None)
    await _ask_expires(message, state, edit=False)


# ---------- 4. Срок годности (необязательно) ----------


async def _ask_expires(message: Message, state: FSMContext, *, edit: bool) -> None:
    await state.set_state(MedAdd.expires)
    text = (
        "📅 До какого времени годен препарат?\n\n"
        "Напиши как на упаковке: <b>ММ.ГГГГ</b> (например, 05.2027) или полную дату "
        "<b>ДД.ММ.ГГГГ</b>.\n"
        f"За {NOTIFY_DAYS_BEFORE} дней до конца срока я напомню. Можно пропустить — тогда напоминаний не будет."
    )
    if edit:
        await safe_edit_text(message, text, reply_markup=skip_keyboard())
    else:
        await render(message, state, text, reply_markup=skip_keyboard())


@router.callback_query(StateFilter(MedAdd.expires), F.data == "medskip")
async def add_expires_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(expires_at=None)
    await _ask_comment(callback.message, state, edit=True)


@router.message(StateFilter(MedAdd.expires), F.text, ~F.text.in_(MENU_BUTTONS))
async def add_expires_text(message: Message, state: FSMContext) -> None:
    await delete_user_message(message)
    expires_at = medicine_service.parse_expiry(message.text)
    if expires_at is None:
        await render(
            message, state, "⚠️ Не разобрала дату. Напиши в формате ММ.ГГГГ или ДД.ММ.ГГГГ, например: 05.2027",
            reply_markup=skip_keyboard(),
        )
        return
    await state.update_data(expires_at=expires_at.isoformat())
    await _ask_comment(message, state, edit=False)


# ---------- 5. Комментарий (необязательно) ----------


async def _ask_comment(message: Message, state: FSMContext, *, edit: bool) -> None:
    await state.set_state(MedAdd.comment)
    text = "💬 Комментарий (необязательно)\n\nНапример: «от тли и трипсов, повторять через 7–10 дней». Можно пропустить."
    if edit:
        await safe_edit_text(message, text, reply_markup=skip_keyboard())
    else:
        await render(message, state, text, reply_markup=skip_keyboard())


@router.callback_query(StateFilter(MedAdd.comment), F.data == "medskip")
async def add_comment_skip(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await _finish_add(callback.message, state, user_id, None, edit=True)


@router.message(StateFilter(MedAdd.comment), F.text, ~F.text.in_(MENU_BUTTONS))
async def add_comment_text(message: Message, state: FSMContext, user_id: int) -> None:
    comment = message.text.strip()
    await delete_user_message(message)
    if len(comment) > MAX_COMMENT_LENGTH:
        await render(
            message, state, f"⚠️ Слишком длинно — не больше {MAX_COMMENT_LENGTH} символов. Попробуй ещё раз.",
            reply_markup=skip_keyboard(),
        )
        return
    await _finish_add(message, state, user_id, comment or None, edit=False)


# ---------- Сохранение ----------


def _expiry_note(expires_at: date | None, today: date) -> str:
    if expires_at is None:
        return "Срок годности не указан — напоминаний не будет."
    left = medicine_service.days_left(expires_at, today)
    if left < 0:
        return "⛔ Срок годности уже истёк — напомню об этом в ближайшее время."
    if left <= NOTIFY_DAYS_BEFORE:
        return f"⚠️ До конца срока меньше {NOTIFY_DAYS_BEFORE} дней — напомню в ближайшее время."
    return f"Напомню за {NOTIFY_DAYS_BEFORE} дней до конца срока."


async def _finish_add(message: Message, state: FSMContext, user_id: int, comment: str | None, *, edit: bool) -> None:
    data = await state.get_data()
    tracked_id = await pop_tracked(state)
    await state.clear()
    expires_at = date.fromisoformat(data["expires_at"]) if data.get("expires_at") else None

    async with get_session() as session:
        try:
            medicine = await medicine_service.add_medicine(
                session,
                user_id,
                data["name"],
                data["kind"],
                data.get("active_substance"),
                expires_at,
                comment,
            )
        except medicine_service.TooManyMedicines:
            notice = f"⚠️ В аптечке уже {medicine_service.MAX_MEDICINES} препаратов — удали ненужные, чтобы добавить новый."
        else:
            note = _expiry_note(expires_at, medicine_service.today_utc())
            notice = f"✅ «{escape(medicine.name)}» добавлен в аптечку. {note}"

    text, kb = await menu_view(user_id, notice)
    if edit:
        await safe_edit_text(message, text, reply_markup=kb)
        return
    if tracked_id:
        await safe_delete_message(message.bot, message.chat.id, tracked_id)
    await message.answer(text, reply_markup=kb)
