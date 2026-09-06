from fastapi import APIRouter, Depends
from fastapi.requests import Request

from admin.auth import require_auth
from admin.database import get_session
from admin.templating import templates
from bot.db import crud
from bot.db.crud.ai_logs import FILTER_ERROR, FILTER_UNKNOWN

router = APIRouter()

PAGE_SIZE = 50


@router.get("/ai-logs")
async def ai_logs_list(
    request: Request,
    page: int = 1,
    action: str | None = None,
    _: str = Depends(require_auth),
):
    """Последние обращения к ИИ-агенту по всем пользователям — чтобы видеть
    реальные формулировки и промахи распознавания вне контекста конкретного
    пользователя. С пагинацией (иначе логи старше limit молча пропадают из
    UI) и фильтром по action/error/unknown — самая частая задача на этой
    странице — найти все промахи агента, а не листать их глазами."""
    page = max(page, 1)
    action_filter = action or None

    async with get_session() as session:
        total = await crud.count_ai_logs_all(session, action_filter)
        offset = (page - 1) * PAGE_SIZE
        entries = await crud.list_ai_logs_all(
            session, limit=PAGE_SIZE, offset=offset, action_filter=action_filter
        )
        known_actions = await crud.list_ai_log_actions(session)

    total_pages = max(1, -(-total // PAGE_SIZE))  # ceil-деление без импорта math
    page = min(page, total_pages)

    return templates.TemplateResponse(
        request,
        "ai_logs.html",
        {
            "entries": entries,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "action_filter": action_filter,
            "known_actions": known_actions,
            "filter_error": FILTER_ERROR,
            "filter_unknown": FILTER_UNKNOWN,
        },
    )
