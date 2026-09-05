"""Сценарий добавления растения через ИИ-агента.

Всегда идёт через явное подтверждение группы — ИИ только предполагает,
финальное слово за пользователем:

  1) (если есть дубль по имени в любой группе) "уже есть — добавить ещё раз?"
  2) "Добавить «X» в группу «Y»?" [Добавить] [Другая группа]
                                   [Отменить]
  3) "Другая группа" -> список всех групп пользователя + "Назад"
     (после выбора — снова экран №2, но уже с новой группой)

Ни на каком шаге новая группа автоматически не создаётся — только выбор
между уже существующими группами или "Без группы" (кроме случая, когда
пользователь сам явно назвал ещё не существующую группу — тогда она
предлагается к созданию прямо на экране №2 и создаётся по факту "Добавить",
см. show_confirm_group / ai_confirm_add).
"""

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group
from bot.keyboards.inline import groups_keyboard
from bot.services import plant_service

from . import router
from .common import reply
from .keyboards import confirm_group_keyboard, duplicate_keyboard
from .states import AIAdd


def _success_message(
    plant_name: str, group_name: str | None, group_id: int | None
) -> tuple[str, InlineKeyboardBuilder]:
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


def match_group(existing_groups: list[Group], ai_group_name: str | None) -> Group | None:
    if not ai_group_name:
        return None
    return next(
        (g for g in existing_groups if g.name.strip().lower() == ai_group_name.strip().lower()), None
    )


async def show_confirm_group(
    reply_target: Message | CallbackQuery,
    state: FSMContext,
    user_id: int,
    name: str,
    comment: str | None,
    group_id: int | None,
    new_group_name: str | None = None,
    force: bool = False,
) -> None:
    """group_id — существующая группа. new_group_name — группа, которую
    явно назвал (или предложил ИИ) пользователь, но её ещё нет в базе:
    показываем это в тексте подтверждения, а саму группу создаём только
    по факту нажатия "Добавить" (см. ai_confirm_add), чтобы отмена на
    этом шаге не оставляла в базе пустую группу."""
    group_name = None
    if group_id is not None:
        async with get_session() as session:
            group = await crud.get_group(session, group_id, user_id)
        group_name = group.name if group else None
        group_id = group.id if group else None  # группу могли удалить между шагами
        if group_id is None:
            new_group_name = None  # группу удалили — раз уж на то пошло, сбрасываем и подсказку

    await state.set_state(AIAdd.confirm_group)
    await state.update_data(name=name, comment=comment, group_id=group_id, new_group_name=new_group_name, force=force)

    if group_id is not None:
        where = f"группу «{group_name}»"
    elif new_group_name:
        where = f"новую группу «{new_group_name}» (создам её)"
    else:
        where = "«Без группы»"
    text = f"🌱 Добавить «{name}» в {where}?"
    await reply(reply_target, text, confirm_group_keyboard().as_markup())


async def show_pick_group(reply_target: Message | CallbackQuery, state: FSMContext, user_id: int) -> None:
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
    await reply(reply_target, "📁 В какую группу добавить?", markup)


async def handle_add_intent(
    message: Message, state: FSMContext, user_id: int, existing_groups: list[Group], intent: dict
) -> None:
    """Точка входа из entrypoint.handle_free_text для action == "add"."""
    plant_name = intent["plant_name"]
    comment = intent.get("comment")
    matched_group = match_group(existing_groups, intent.get("group_name"))
    new_group_name = None
    if not matched_group and intent.get("group_name"):
        new_group_name = intent["group_name"].strip() or None

    async with get_session() as session:
        existing_plant = await crud.find_plant_by_name_any_group(session, user_id, plant_name)

    if existing_plant:
        await state.set_state(AIAdd.confirm_duplicate)
        await state.update_data(
            name=plant_name,
            comment=comment,
            group_id=matched_group.id if matched_group else None,
            new_group_name=new_group_name,
        )
        await message.answer(
            f"⚠️ «{existing_plant.name}» уже есть в списке. Добавить ещё один экземпляр?",
            reply_markup=duplicate_keyboard().as_markup(),
        )
        return

    await show_confirm_group(
        message,
        state,
        user_id,
        plant_name,
        comment,
        matched_group.id if matched_group else None,
        new_group_name=new_group_name,
    )


@router.callback_query(StateFilter(AIAdd.confirm_duplicate), F.data == "aiaddforce")
async def ai_add_force(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Пользователь подтвердил добавление, несмотря на найденный дубль по
    имени — идёт та же самая процедура подтверждения группы, что и обычно,
    только с force=True, чтобы финальная проверка дубля в конкретной
    группе не сработала повторно."""
    await callback.answer()
    data = await state.get_data()
    await show_confirm_group(
        callback,
        state,
        user_id,
        data["name"],
        data.get("comment"),
        data.get("group_id"),
        new_group_name=data.get("new_group_name"),
        force=True,
    )


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiconfirmadd")
async def ai_confirm_add(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    group_id = data.get("group_id")
    new_group_name = data.get("new_group_name")

    if group_id is None and new_group_name:
        # Группу явно назвал пользователь (или предложил ИИ), но её не было в
        # базе — создаём её только сейчас, по факту подтверждения, чтобы
        # нажатие "Отменить" на предыдущем шаге не оставляло пустую группу.
        async with get_session() as session:
            group, _ = await crud.get_or_create_group(session, user_id, new_group_name)
            await session.commit()
            group_id = group.id

    text, markup = await _perform_add(
        user_id, data["name"], group_id, data.get("comment"), force=data.get("force", False)
    )
    await state.clear()
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(StateFilter(AIAdd.confirm_group), F.data == "aiothergroup")
async def ai_other_group(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    await show_pick_group(callback, state, user_id)


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
    await show_confirm_group(
        callback, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.message(StateFilter(AIAdd.new_group_name), F.text)
async def ai_new_group_name(message: Message, state: FSMContext, user_id: int) -> None:
    async with get_session() as session:
        group, _ = await crud.get_or_create_group(session, user_id, message.text.strip())
        await session.commit()
        group_id = group.id

    data = await state.get_data()
    await show_confirm_group(
        message, state, user_id, data["name"], data.get("comment"), group_id, force=data.get("force", False)
    )


@router.callback_query(StateFilter(AIAdd.pick_group), F.data == "aibacktoconfirm")
async def ai_back_to_confirm(callback: CallbackQuery, state: FSMContext, user_id: int) -> None:
    await callback.answer()
    data = await state.get_data()
    await show_confirm_group(
        callback,
        state,
        user_id,
        data["name"],
        data.get("comment"),
        data.get("group_id"),
        new_group_name=data.get("new_group_name"),
        force=data.get("force", False),
    )


@router.callback_query(
    StateFilter(AIAdd.confirm_duplicate, AIAdd.confirm_group, AIAdd.pick_group, AIAdd.new_group_name),
    F.data == "aicancel",
)
async def ai_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()
