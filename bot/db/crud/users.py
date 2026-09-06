from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AiLog, Group, Plant, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        await session.flush()
    elif username is not None or full_name is not None:
        if username is not None:
            user.username = username
        if full_name is not None:
            user.full_name = full_name
        await session.flush()
    return user


async def set_ungrouped_label(session: AsyncSession, user: User, label: str | None) -> None:
    """label=None (или пустая строка после .strip()) сбрасывает подпись
    обратно на дефолт "Без группы" — сама "группа" при этом не хранится
    как запись, это просто подпись для растений без group_id."""
    user.ungrouped_label = label.strip() if label and label.strip() else None
    await session.flush()


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars())


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_last_activity_map(session: AsyncSession) -> dict[int, datetime]:
    """Дата последней активности по каждому пользователю — максимум между
    последним добавленным растением и последним обращением к ИИ-агенту
    (ai_logs пишутся даже когда агент не понял запрос, так что это ловит
    и тех, кто просто писал боту, ничего не добавив). Две bulk-агрегации
    вместо N+1 по пользователям, объединяются в памяти — их обычно не
    много, а результат используется только для отображения в списке."""
    plants_result = await session.execute(
        select(Plant.user_id, func.max(Plant.created_at)).group_by(Plant.user_id)
    )
    logs_result = await session.execute(
        select(AiLog.user_id, func.max(AiLog.created_at)).group_by(AiLog.user_id)
    )

    activity: dict[int, datetime] = {}
    for user_id, last_created in [*plants_result.all(), *logs_result.all()]:
        if last_created is None:
            continue
        if user_id not in activity or last_created > activity[user_id]:
            activity[user_id] = last_created
    return activity


async def clear_user_plants(session: AsyncSession, user_id: int) -> None:
    """Безвозвратно удаляет всю базу растений пользователя (группы и
    растения) вместе с кастомной подписью "без группы", но саму запись
    пользователя оставляет — чтобы бот продолжал узнавать его при
    следующем обращении. Удаляем явными bulk-запросами (а не через
    каскад на ORM-уровне), чтобы не зависеть от того, загружены ли
    связи — в асинхронной сессии ленивая подгрузка коллекций на flush
    недоступна. Используется только из админки."""
    await session.execute(delete(Plant).where(Plant.user_id == user_id))
    await session.execute(delete(Group).where(Group.user_id == user_id))
    await session.execute(
        update(User).where(User.id == user_id).values(ungrouped_label=None)
    )
    await session.flush()
