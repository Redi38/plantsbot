from fastapi import APIRouter, Depends
from fastapi.requests import Request

from admin.auth import require_auth
from admin.database import get_session
from admin.templating import templates
from bot.db import crud

router = APIRouter()


@router.get("/ai-logs")
async def ai_logs_list(request: Request, _: str = Depends(require_auth)):
    """Последние обращения к ИИ-агенту по всем пользователям — чтобы видеть
    реальные формулировки и промахи распознавания вне контекста конкретного
    пользователя."""
    async with get_session() as session:
        entries = await crud.list_ai_logs_all(session, limit=200)
    return templates.TemplateResponse(
        "ai_logs.html", {"request": request, "entries": entries}
    )
