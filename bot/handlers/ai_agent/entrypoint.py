"""Ловит свободный текст (не команду, вне FSM-сценариев) и пытается понять
намерение через ai_service. Работает только если AI_ENABLED=true.

Этот роутер нужно регистрировать ПОСЛЕДНИМ в диспетчере — так все команды
и активные FSM-сценарии (add/rename/import) успеют перехватить сообщение
раньше (см. StateFilter(None) ниже — handle_free_text сработает только вне
них).

Сама диспетчеризация по action — только здесь; логика каждого сценария
живёт в соседних модулях этого пакета (add_flow / delete_flow /
group_actions), см. их докстринги.
"""

import logging

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import config
from bot.db import crud
from bot.db.database import get_session
from bot.services import ai_service

from . import router
from .add_flow import handle_add_intent
from .delete_flow import handle_delete_intent
from .group_actions import handle_create_group_intent, handle_delete_group_intent

logger = logging.getLogger(__name__)


@router.message(StateFilter(None), F.text)
async def handle_free_text(message: Message, state: FSMContext, user_id: int) -> None:
    if not config.ai_enabled:
        return

    async with get_session() as session:
        groups, ungrouped = await crud.get_full_tree(session, user_id)
    existing_group_names = [g.name for g in groups]
    all_plants = ungrouped[:] + [p for g in groups for p in g.plants]
    existing_plant_names = list({p.name.strip().lower(): p.name for p in all_plants}.values())

    try:
        intent = await ai_service.parse_intent(
            message.text,
            existing_groups=existing_group_names,
            existing_plants=existing_plant_names,
            user_id=user_id,
        )
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
        await handle_add_intent(message, state, user_id, groups, intent)
        return

    if action == "delete_group" and intent.get("group_name"):
        await handle_delete_group_intent(message, user_id, intent)
        return

    if action == "create_group" and intent.get("group_name"):
        await handle_create_group_intent(message, user_id, intent)
        return

    if action == "delete" and intent.get("plant_name"):
        await handle_delete_intent(message, state, user_id, intent, groups=groups, ungrouped=ungrouped)
        return

    await message.answer(
        "Не совсем поняла, что нужно сделать 🤔 Используй кнопки внизу экрана"
    )
