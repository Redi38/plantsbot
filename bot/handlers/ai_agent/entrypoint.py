"""Ловит свободный текст (не команду, вне FSM-сценариев) и пытается понять
намерение через ai_service. Работает только если AI_ENABLED=true.

Этот роутер нужно регистрировать ПОСЛЕДНИМ в диспетчере — так все команды
и активные FSM-сценарии (add/rename/import) успеют перехватить сообщение
раньше (см. StateFilter(None) ниже — handle_free_text сработает только вне
них).

Сама диспетчеризация по action — только здесь; логика каждого сценария
живёт в соседних модулях этого пакета (add_flow / delete_flow /
group_actions), см. их докстринги. Диспетчеризация устроена как словарь
action -> обработчик (см. _ACTION_HANDLERS ниже): каждый обработчик
получает единый _Intent-контекст и возвращает True, если разобрался с
запросом (в т.ч. когда результат — сообщение об ошибке пользователю),
или False, если для этого action в intent не хватило обязательных полей
(например action="add" без plant_name) — тогда падаем в общий ответ
"не поняла" в конце handle_free_text.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import config
from bot.db import crud
from bot.db.database import get_session
from bot.db.models import Group, Plant
from bot.handlers.list_view import group_menu_text_and_kb, send_group_page
from bot.services import ai_service, plant_service
from bot.utils.chat import begin_dialog, safe_delete_message

from . import router
from .add_flow import handle_add_intent, match_group
from .common import reply_group_not_found, resolve_group
from .delete_flow import handle_delete_intent
from .edit_flow import handle_edit_plant_intent
from .group_actions import handle_create_group_intent, handle_delete_group_intent, handle_rename_group_intent

logger = logging.getLogger(__name__)


@dataclass
class _Ctx:
    """Общий контекст для всех обработчиков action — вместо того, чтобы у
    каждого была своя сигнатура, они принимают один объект и сами берут
    из него, что нужно."""

    message: Message
    state: FSMContext
    user_id: int
    intent: dict
    groups: list[Group]
    ungrouped: list[Plant]
    all_plants: list[Plant]


async def _dispatch_add(ctx: _Ctx) -> bool:
    if not ctx.intent.get("plant_name"):
        return False
    await handle_add_intent(ctx.message, ctx.state, ctx.user_id, ctx.groups, ctx.intent)
    return True


async def _dispatch_delete_group(ctx: _Ctx) -> bool:
    if not ctx.intent.get("group_name"):
        return False
    await handle_delete_group_intent(ctx.message, ctx.user_id, ctx.intent)
    return True


async def _dispatch_create_group(ctx: _Ctx) -> bool:
    if not ctx.intent.get("group_name"):
        return False
    await handle_create_group_intent(ctx.message, ctx.user_id, ctx.intent)
    return True


async def _dispatch_rename_group(ctx: _Ctx) -> bool:
    if not (ctx.intent.get("group_name") and ctx.intent.get("new_name")):
        return False
    await handle_rename_group_intent(ctx.message, ctx.user_id, ctx.intent)
    return True


async def _dispatch_delete(ctx: _Ctx) -> bool:
    if not (ctx.intent.get("plant_name") or ctx.intent.get("group_name")):
        return False
    await handle_delete_intent(
        ctx.message, ctx.state, ctx.user_id, ctx.intent, groups=ctx.groups, ungrouped=ctx.ungrouped
    )
    return True


async def _dispatch_edit_plant(ctx: _Ctx) -> bool:
    if not ctx.intent.get("plant_name"):
        return False
    await handle_edit_plant_intent(
        ctx.message, ctx.state, ctx.user_id, ctx.intent, groups=ctx.groups, ungrouped=ctx.ungrouped
    )
    return True


async def _dispatch_list(ctx: _Ctx) -> bool:
    old_msg_id = await begin_dialog(ctx.state)
    if old_msg_id:
        await safe_delete_message(ctx.message.bot, ctx.message.chat.id, old_msg_id)

    filter_term = (ctx.intent.get("group_name") or "").strip()
    if not filter_term:
        text, kb = await group_menu_text_and_kb(ctx.user_id)
        await ctx.message.answer(text, reply_markup=kb)
        return True

    matched_group = match_group(ctx.groups, filter_term)
    if not matched_group:
        async with get_session() as session:
            matched_group, candidates = await resolve_group(session, ctx.user_id, filter_term)
        if not matched_group and candidates:
            await reply_group_not_found(ctx.message, filter_term, candidates)
            return True

    if matched_group:
        await send_group_page(ctx.message, ctx.user_id, str(matched_group.id))
        return True

    matched_names = ctx.intent.get("matched_plants") or []
    if isinstance(matched_names, str):
        matched_names = [matched_names]
    if not isinstance(matched_names, list):
        matched_names = []
    by_name = {p.name.strip().lower(): p for p in ctx.all_plants}
    term_matches = [
        by_name[n.strip().lower()] for n in matched_names if isinstance(n, str) and n.strip().lower() in by_name
    ]

    if not term_matches:
        async with get_session() as session:
            term_matches = await plant_service.find_plants_by_term(session, ctx.user_id, filter_term)

    if term_matches:
        await ctx.message.answer(plant_service.render_term_matches(filter_term, term_matches))
        return True

    await ctx.message.answer(f'Не нашла ничего похожего на "{filter_term}". Проверь 📋 Список')
    return True


_ACTION_HANDLERS: dict[str, Callable[[_Ctx], Awaitable[bool]]] = {
    "add": _dispatch_add,
    "delete_group": _dispatch_delete_group,
    "create_group": _dispatch_create_group,
    "rename_group": _dispatch_rename_group,
    "delete": _dispatch_delete,
    "edit_plant": _dispatch_edit_plant,
    "list": _dispatch_list,
}


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

    handler = _ACTION_HANDLERS.get(action) if action else None
    if handler:
        ctx = _Ctx(message, state, user_id, intent, groups, ungrouped, all_plants)
        if await handler(ctx):
            return

    await message.answer(
        "Не совсем поняла, что нужно сделать 🤔 Используй кнопки внизу экрана"
    )
