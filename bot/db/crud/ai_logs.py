from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AiLog, User


async def create_ai_log(
    session: AsyncSession,
    user_id: int,
    user_text: str,
    action: str | None = None,
    plant_name: str | None = None,
    group_name: str | None = None,
    comment: str | None = None,
    error: str | None = None,
) -> AiLog:
    log = AiLog(
        user_id=user_id,
        user_text=user_text,
        action=action,
        plant_name=plant_name,
        group_name=group_name,
        comment=comment,
        error=error,
    )
    session.add(log)
    await session.flush()
    return log


async def list_ai_logs_for_user(session: AsyncSession, user_id: int, limit: int = 50) -> list[AiLog]:
    result = await session.execute(
        select(AiLog).where(AiLog.user_id == user_id).order_by(AiLog.id.desc()).limit(limit)
    )
    return list(result.scalars())


async def list_ai_logs_all(session: AsyncSession, limit: int = 100) -> list[tuple[AiLog, User | None]]:
    """Последние обращения к ИИ по всем пользователям сразу — для общего
    обзора в админке (что чаще всего пишут, где агент промахивается).
    User подтягивается через outerjoin, а не relationship: у AiLog
    намеренно нет ForeignKey на users (см. docstring модели), чтобы лог
    переживал удаление пользователя."""
    result = await session.execute(
        select(AiLog, User)
        .outerjoin(User, User.id == AiLog.user_id)
        .order_by(AiLog.id.desc())
        .limit(limit)
    )
    return [(log, user) for log, user in result.all()]
