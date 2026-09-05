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
from bot.handlers.list_view import group_menu_text_and_kb, send_group_page
from bot.services import ai_service, plant_service
from bot.utils.chat import begin_dialog, safe_delete_message

from . import router
from .add_flow import handle_add_intent, match_group
from .delete_flow import handle_delete_intent
from .edit_flow import handle_edit_plant_intent
from .group_actions import handle_create_group_intent, handle_delete_group_intent, handle_rename_group_intent

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
    except ai_service.AIServiceRateLimited as exc:
        logger.warning("AI-агент: провайдер превысил лимит запросов: %s", exc)
        async with get_session() as session:
            await crud.create_ai_log(session, user_id, message.text, error=str(exc))
            await session.commit()
        await message.answer(
            "⏳ Сейчас слишком много запросов к ИИ, попробуй ещё раз через минуту "
            "или используй кнопки ➕ Добавить / 📋 Список внизу экрана."
        )
        return
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

    if action == "rename_group" and intent.get("group_name") and intent.get("new_name"):
        await handle_rename_group_intent(message, user_id, intent)
        return

    if action == "delete" and (intent.get("plant_name") or intent.get("group_name")):
        await handle_delete_intent(message, state, user_id, intent, groups=groups, ungrouped=ungrouped)
        return

    if action == "edit_plant" and intent.get("plant_name"):
        await handle_edit_plant_intent(message, state, user_id, intent, groups=groups, ungrouped=ungrouped)
        return

    if action == "list":
        old_msg_id = await begin_dialog(state)
        if old_msg_id:
            await safe_delete_message(message.bot, message.chat.id, old_msg_id)

        filter_term = (intent.get("group_name") or "").strip()
        if not filter_term:
            text, kb = await group_menu_text_and_kb(user_id)
            await message.answer(text, reply_markup=kb)
            return

        matched_group = match_group(groups, filter_term)
        if not matched_group:
            async with get_session() as session:
                candidates = await crud.find_groups_fuzzy(session, user_id, filter_term)
            if len(candidates) == 1:
                matched_group = candidates[0]
            elif len(candidates) > 1:
                names = ", ".join(f"«{g.name}»" for g in candidates)
                await message.answer(f"Нашла несколько похожих групп: {names}. Уточни название точнее.")
                return

        if matched_group:
            await send_group_page(message, user_id, str(matched_group.id))
            return

        matched_names = intent.get("matched_plants") or []
        if isinstance(matched_names, str):
            matched_names = [matched_names]
        if not isinstance(matched_names, list):
            matched_names = []
        by_name = {p.name.strip().lower(): p for p in all_plants}
        term_matches = [
            by_name[n.strip().lower()]
            for n in matched_names
            if isinstance(n, str) and n.strip().lower() in by_name
        ]

        if not term_matches:
            async with get_session() as session:
                term_matches = await plant_service.find_plants_by_term(session, user_id, filter_term)

        if term_matches:
            await message.answer(plant_service.render_term_matches(filter_term, term_matches))
            return

        await message.answer(f'Не нашла ничего похожего на "{filter_term}". Проверь 📋 Список')
        return

    await message.answer(
        "Не совсем поняла, что нужно сделать 🤔 Используй кнопки внизу экрана"
    )
