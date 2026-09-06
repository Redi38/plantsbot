"""Сопоставление группы, предложенной ИИ, и экраны выбора группы.

Ни на каком шаге новая группа автоматически не создаётся — только выбор
между уже существующими группами или "Без группы" (кроме случая, когда
пользователь сам явно назвал ещё не существующую группу — тогда она
предлагается к созданию прямо на экране подтверждения и создаётся по
факту "Добавить", см. show_confirm_group / handlers.ai_confirm_add).
"""

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group
from bot.keyboards.inline import groups_keyboard

from ..common import reply
from ..keyboards import confirm_group_keyboard
from ..states import AIAdd


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
    по факту нажатия "Добавить" (см. handlers.ai_confirm_add), чтобы отмена
    на этом шаге не оставляла в базе пустую группу."""
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
