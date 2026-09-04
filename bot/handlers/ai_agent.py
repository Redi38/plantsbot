"""
Ловит свободный текст (не команду, вне FSM-сценариев) и пытается понять
намерение через ai_service. Работает только если AI_ENABLED=true.

Этот роутер нужно регистрировать ПОСЛЕДНИМ в диспетчере — так все команды
и активные FSM-сценарии (add/rename/import) успеют перехватить сообщение раньше.

Сценарий добавления растения через ИИ всегда идёт через явное подтверждение
группы — ИИ только предполагает, финальное слово за пользователем:

  1) (если есть дубль по имени в любой группе) "уже есть — добавить ещё раз?"
  2) "Добавить «X» в группу «Y»?" [Добавить] [Другая группа]
                                   [Отменить]
  3) "Другая группа" -> список всех групп пользователя + "Назад"
     (после выбора — снова экран №2, но уже с новой группой)

Ни на каком шаге новая группа автоматически не создаётся — только выбор
между уже существующими группами или "Без группы".
"""

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group
from bot.keyboards.inline import groups_keyboard
from bot.services import ai_service, plant_service

router = Router(name="ai_agent")
logger = logging.getLogger(__name__)


class AIAdd(StatesGroup):
    confirm_duplicate = State()  # "уже есть в списке — добавить ещё раз?"
    confirm_group = State()      # "добавить «X» в группу «Y»?"
    pick_group = State()         # список всех групп на выбор
    new_group_name = State()     # ввод названия новой группы


def _success_message(plant_name: str, group_name: str | None, group_id: int | None) -> tuple[str, InlineKeyboardBuilder]:
    group_part = f" в группу «{group_name}»" if group_name else ""
    group_token = "none" if group_id is None else str(group_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список", callback_data=f"lg:{group_token}", style="primary")
    return f"🌱 Добавила «{plant_name}»{group_part}", builder


async def _perform_add(
    user_id: int, name: str, group_id: int | None, comment: str | None, force: bool = False
) -> tuple[str, object]:
    """force=True пропускает проверку на дубль совсем — используется, когда
    пользователь уже подтвердил добавление повтора раньше (кнопка
    "Всё равно добавить"), чтобы та же проверка не сработала ещё раз для
    группы, выбранной уже после этого."""
    async with get_session() as session:
        try:
            plant = await plant_service.add_plant(
                session, user_id, name=name, group_id=group_id, comment=comment, force=force
            )
        except plant_service.DuplicatePlantError as exc:
            return f"⚠️ «{exc.existing.name}» уже есть в этом списке — не добавляю повторно", None
        group_name = None
        if plant.group_id is not None:
            group = await crud.get_group(session, plant.group_id, user_id)
            group_name = group.name if group else None
    text, builder = _success_message(plant.name, group_name, plant.group_id)
    return text, builder.as_markup()


def _match_group(existing_groups: list[Group], ai_group_name: str | None) -> Group | None:
    if not ai_group_name:
        return None
    return next(
        (g for g in existing_groups if g.name.strip().lower() == ai_group_name.strip().lower()), None
    )


def _confirm_group_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Добавить", callback_data="aiconfirmadd", style="success")
    builder.button(text="📁 Другая группа", callback_data="aiothergroup", style="primary")
    builder.button(text="❌ Отменить", callback_data="aicancel", style="danger")
    builder.adjust(2, 1)
    return builder


def _duplicate_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Всё равно добавить", callback_data="aiaddforce", style="primary")
    builder.button(text="❌ Отмена", callback_data="aicancel", style="danger")
    builder.adjust(1)
    return builder


async def _reply(reply_target: Message | CallbackQuery, text: str, markup) -> None:
    if isinstance(reply_target, CallbackQuery):
        await reply_target.message.edit_text(text, reply_markup=markup)
    else:
        await reply_target.answer(text, reply_markup=markup)


async def _show_confirm_group(
    reply_target: Message | CallbackQuery,
    state: FSMContext,
    user_id: int,
    name: str,
    comment: str | None,
    group_id: int | None,
    force: bool = False,
) -> None:
    group_name = None
    if group_id is not None:
        async with get_session() as session:
            group = await crud.get_group(session, group_id, user_id)
        group_name = group.name if group else None
        group_id = group.id if group else None  # группу могли удалить между шагами

    await state.set_state(AIAdd.confirm_group)
    await state.update_data(name=name, comment=comment, group_id=group_id, force=force)

    where = f"группу «{group_name}»" if group_name else "«Без группы»"
    text = f"🌱 Добавить «{name}» в {where}?"
    await _reply(reply_target, text, _confirm_group_keyboard().as_markup())


async def _show_pick_group(reply_target: Message | CallbackQuery, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        existing_groups = await crud.list_groups(session, user_id)

    await state.set_state(AIAdd.pick_group)
    markup = groups_keyboard(
        existing_groups,
        prefix="aipickgrp",
        none_label="Без группы",
        allow_new=True,
        new_label="➕ Новая группа",
        back_data="aibacktoconfirm",
    )
    await _reply(reply_target, "📁 В какую группу добавить?", markup)


@router.message(StateFilter(None), F.text)
async def handle_free_text(message: Message, state: FSMContext, user_id: int) -> None:
    if not config.ai_enabled:
        return

    async with get_session() as session:
        existing_groups = await crud.list_groups(session, user_id)
    existing_group_names = [g.name for g in existing_groups]

    try:
        intent = await ai_service.parse_intent(message.text, existing_groups=existing_group_names)
    except ai_service.AIServiceUnavailable as exc:
        logger.warning("AI-агент недоступен: %s", exc)
        async with get_session() as session:
            await crud.create_ai_log(session, user_id, message.text, error=str(exc))
            await session.commit()
        await message.answer(
            "Не поняла запрос. Используй кнопки ➕ Добавить или 📋 Список внизу экрана."
        )
        return

    action = intent.get("action")

    async with get_session() as session:
        await crud.create_ai_log(
            session,
            user_id,
            message.text,
            action=action,
            plant_name=intent.get("plant_name"),
            group_name=intent.get("group_name"),
            comment=intent.get("comment"),
        )
        await session.commit()

    if action == "add" and intent.get("plant_name"):
        plant_name = intent["plant_name"]
        comment = intent.get("comment")
        matched_group = _match_group(existing_groups, intent.get("group_name"))

        async with get_session() as session:
            existing_plant = await crud.find_plant_by_name_any_group(session, user_id, plant_name)

        if existing_plant:
            await state.set_state(AIAdd.confirm_duplicate)
            await state.update_data(
                name=plant_name,
                comment=comment,
                group_id=matched_group.id if matched_group else None,
            )
            await message.answer(
                f"⚠️ «{existing_plant.name}» уже есть в списке. Добавить ещё один экземпляр?",
                reply_markup=_duplicate_keyboard().as_markup(),
            )
            return

        await _show_confirm_group(
            message, state, user_id, plant_name, comment, matched_group.id if matched_group else None
        )
        return

    if action == "delete" and intent.get("plant_name"):
        async with get_session() as session:
            groups, ungrouped = await crud.get_full_tree(session, user_id)

        all_plants = ungrouped[:] + [p for g in groups for p in g.plants]
        matches = [p for p in all_plants if p.name.lower() == intent["plant_name"].strip().lower()]

        if not matches:
            await message.answer(
                f"Не нашла растение «{intent['plant_name']}». Проверь 📋 Список или удали вручную из списка"
            )
            return
        if len(matches) > 1:
            await message.answer(
                f"Нашла несколько растений с именем «{intent['plant_name']}» — удали вручную из списка"
            )
            return

        async with get_session() as session:
            plant = await crud.get_plant(session, matches[0].id, user_id)
            await plant_service.remove_plant(session, plant)
        await message.answer(f"🗑 Удалила «{matches[0].name}»")
        return

    await message.answer(
        "Не совсем поняла, что нужно сделать 🤔 Используй кнопки внизу экрана"
    )


@router.callback_query(StateFilter(AIAdd.confirm_duplicate), F.data == "aiaddforce")
async def ai_add_force(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Пользователь подтвердил добавление, несмотря на найденный дубль по
    имени — идёт та же самая процедура подтверждения группы, что и обычно,
    только с force=True, чтобы финальная проверка дубля в конкретной
    группе не сработала повторно."""
    await callback.answer()
    data = await state.get_data()
    await _show_confirm_group(
        callback, state, user_id, data["name"], data.get("comment"), data.get("group_id"), force=True
    )


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiconfirmadd")
async def ai_confirm_add(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    text, markup = await _perform_add(
        user_id, data["name"], data.get("group_id"), data.get("comment"), force=data.get("force", False)
    )
    await state.clear()
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiothergroup")
async def ai_other_group(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await _show_pick_group(callback, state, user_id)


@router.callback_query(StateFilter(AIAdd.pick_group), F.data.startswith("aipickgrp:"))
async def ai_pick_group(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    value = callback.data.split(":", 1)[1]
    await callback.answer()

    if value == "new":
        await state.set_state(AIAdd.new_group_name)
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Отменить", callback_data="aicancel", style="danger")
        await callback.message.edit_text("🆕 Название новой группы?", reply_markup=builder.as_markup())
        return

    group_id = None if value == "none" else int(value)
    data = await state.get_data()
    await _show_confirm_group(
        callback, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.message(StateFilter(AIAdd.new_group_name), F.text)
async def ai_new_group_name(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        group, _ = await crud.get_or_create_group(session, user_id, message.text.strip())
        await session.commit()
        group_id = group.id

    data = await state.get_data()
    await _show_confirm_group(
        message, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.callback_query(StateFilter(AIAdd.pick_group), F.data == "aibacktoconfirm")
async def ai_back_to_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    await _show_confirm_group(
        callback, state, user_id, data["name"], data.get("comment"), data.get("group_id"), force=data.get("force", False)
    )


@router.callback_query(
    StateFilter(AIAdd.confirm_duplicate, AIAdd.confirm_group, AIAdd.pick_group, AIAdd.new_group_name),
    F.data == "aicancel",
)
async def ai_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
