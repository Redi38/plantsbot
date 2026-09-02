"""
Ловит свободный текст (не команду, вне FSM-сценариев) и пытается понять
намерение через ai_service. Работает только если AI_ENABLED=true.

Этот роутер нужно регистрировать ПОСЛЕДНИМ в диспетчере — так все команды
и активные FSM-сценарии (add/rename/import) успеют перехватить сообщение раньше.
"""

from aiogram import F, Router
from aiogram.types import Message

from bot.config import config
from bot.db import crud
from bot.db.database import get_session
from bot.services import ai_service, plant_service

router = Router(name="ai_agent")


@router.message(F.text)
async def handle_free_text(message: Message) -> None:
    if not config.ai_enabled:
        return

    try:
        intent = await ai_service.parse_intent(message.text)
    except ai_service.AIServiceUnavailable:
        await message.answer(
            "Не поняла запрос. Используй кнопки ➕ Добавить, 🗑 Удалить или ℹ️ Помощь внизу экрана."
        )
        return

    action = intent.get("action")

    if action == "add" and intent.get("plant_name"):
        async with get_session() as session:
            user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
            plant = await plant_service.add_plant(
                session,
                user.id,
                name=intent["plant_name"],
                group_name=intent.get("group_name"),
                comment=intent.get("comment"),
            )
        group_part = f" в группу «{intent['group_name']}»" if intent.get("group_name") else ""
        await message.answer(f"🌱 Добавила «{plant.name}»{group_part}")
        return

    if action == "delete" and intent.get("plant_name"):
        async with get_session() as session:
            user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
            groups, ungrouped = await crud.get_full_tree(session, user.id)

        all_plants = ungrouped[:] + [p for g in groups for p in g.plants]
        matches = [p for p in all_plants if p.name.lower() == intent["plant_name"].strip().lower()]

        if not matches:
            await message.answer(
                f"Не нашла растение «{intent['plant_name']}». Проверь 📋 Список или удали вручную кнопкой 🗑 Удалить"
            )
            return
        if len(matches) > 1:
            await message.answer(
                f"Нашла несколько растений с именем «{intent['plant_name']}» — удали вручную кнопкой 🗑 Удалить"
            )
            return

        async with get_session() as session:
            user = await crud.get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
            plant = await crud.get_plant(session, matches[0].id, user.id)
            await plant_service.remove_plant(session, plant)
        await message.answer(f"🗑 Удалила «{matches[0].name}»")
        return

    await message.answer(
        "Не совсем поняла, что нужно сделать 🤔 Используй кнопки внизу экрана"
    )
