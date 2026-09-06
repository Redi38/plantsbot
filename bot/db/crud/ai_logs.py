from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AiLog, User

# Значения фильтра по колонке action на странице /ai-logs. "error" и
# "unknown" — не реальные значения action, а отдельные категории поверх
# него: ошибка определяется по AiLog.error IS NOT NULL, а "unknown" — это
# ни ошибка, ни один из известных action'ов (агент не смог сопоставить
# запрос ни одному сценарию, но и не упал с исключением).
FILTER_ERROR = "error"
FILTER_UNKNOWN = "unknown"


def _apply_action_filter(stmt, action_filter: str | None):
    if not action_filter:
        return stmt
    if action_filter == FILTER_ERROR:
        return stmt.where(AiLog.error.is_not(None))
    if action_filter == FILTER_UNKNOWN:
        return stmt.where(AiLog.error.is_(None), AiLog.action.is_(None))
    return stmt.where(AiLog.error.is_(None), AiLog.action == action_filter)


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


async def list_ai_logs_all(
    session: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    action_filter: str | None = None,
) -> list[tuple[AiLog, User | None]]:
    """Последние обращения к ИИ по всем пользователям сразу — для общего
    обзора в админке (что чаще всего пишут, где агент промахивается).
    User подтягивается через outerjoin, а не relationship: у AiLog
    намеренно нет ForeignKey на users (см. docstring модели), чтобы лог
    переживал удаление пользователя. offset/limit — постраничная навигация,
    action_filter — фильтр по действию/ошибке/unknown (см. _apply_action_filter)."""
    stmt = select(AiLog, User).outerjoin(User, User.id == AiLog.user_id)
    stmt = _apply_action_filter(stmt, action_filter)
    stmt = stmt.order_by(AiLog.id.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [(log, user) for log, user in result.all()]


async def count_ai_logs_all(session: AsyncSession, action_filter: str | None = None) -> int:
    stmt = _apply_action_filter(select(func.count(AiLog.id)), action_filter)
    result = await session.execute(stmt)
    return result.scalar_one()


async def list_ai_log_actions(session: AsyncSession) -> list[str]:
    """Список различных значений action, реально встречающихся в логах —
    чтобы фильтр на странице предлагал только те варианты, которые
    что-то найдут, а не захардкоженный список сценариев из кода бота."""
    result = await session.execute(
        select(AiLog.action).where(AiLog.action.is_not(None)).distinct().order_by(AiLog.action)
    )
    return [row[0] for row in result.all()]
