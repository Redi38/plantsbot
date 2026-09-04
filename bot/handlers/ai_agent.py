"""
Ловит свободный текст (не команду, вне FSM-сценариев) и пытается понять
намерение через ai_service. Работает только если AI_ENABLED=true.

Этот роутер нужно регистрировать ПОСЛЕДНИМ в диспетчере — так все команды
и активные FSM-сценарии (add/rename/import) успеют перехватить сообщение раньше.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config
from bot.db import crud
from bot.db.database import get_session
from bot.services import ai_service, plant_service

router = Router(name="ai_agent")
logger = logging.getLogger(__name__)


@router.message(F.text)
async def handle_free_text(message: Message, user_id: int) -> None:
    if not config.ai_enabled:
        return

    try:
        intent = await ai_service.parse_intent(message.text)
    except ai_service.AIServiceUnavailable as exc:
        # Раньше причина падения (код ответа AI API, текст ошибки — всё это
        # есть в самом exc) нигде не логировалась и терялась молча, пользователь
        # получал только общую фразу — из логов бота было не понять, что
        # именно не так (неверный ключ, битый URL, модель недоступна и т.д.).
        logger.warning("AI-агент недоступен: %s", exc)
        await message.answer(
            "Не поняла запрос. Используй кнопки ➕ Добавить или 📋 Список внизу экрана."
        )
        return

    action = intent.get("action")

    if action == "add" and intent.get("plant_name"):
        async with get_session() as session:
            try:
                plant = await plant_service.add_plant(
                    session,
                    user_id,
                    name=intent["plant_name"],
                    group_name=intent.get("group_name"),
                    comment=intent.get("comment"),
                )
            except plant_service.DuplicatePlantError as exc:
                await message.answer(f"⚠️ «{exc.existing.name}» уже есть в этом списке — не добавляю повторно")
                return
        group_part = f" в группу «{intent['group_name']}»" if intent.get("group_name") else ""
        group_token = "none" if plant.group_id is None else str(plant.group_id)
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 Список", callback_data=f"lg:{group_token}")
        await message.answer(f"🌱 Добавила «{plant.name}»{group_part}", reply_markup=builder.as_markup())
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
