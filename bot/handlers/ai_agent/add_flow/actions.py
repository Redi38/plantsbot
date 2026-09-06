"""Собственно добавление растения в базу и сообщение об успехе."""

from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db import crud
from bot.db.database import get_session
from bot.services import plant_service


def success_message(
    plant_name: str, group_name: str | None, group_id: int | None
) -> tuple[str, InlineKeyboardBuilder]:
    group_part = f" в группу «{group_name}»" if group_name else ""
    group_token = "none" if group_id is None else str(group_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список", callback_data=f"lg:{group_token}", style="primary")
    return f"🌱 Добавила «{plant_name}»{group_part}", builder


async def perform_add(
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
    text, builder = success_message(plant.name, group_name, plant.group_id)
    return text, builder.as_markup()
