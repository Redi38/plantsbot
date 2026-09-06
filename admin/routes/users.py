import csv as csv_mod
import io

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import StreamingResponse

from admin.auth import require_auth
from admin.database import get_session
from admin.helpers import get_user_or_404, plant_count, redirect_with, user_redirect
from admin.templating import templates
from bot.db import crud
from bot.services import import_service, plant_service

router = APIRouter()


@router.get("/")
async def users_list(request: Request, _: str = Depends(require_auth)):
    async with get_session() as session:
        users = await crud.list_users(session)
        last_activity = await crud.get_last_activity_map(session)
        cards = []
        for user in users:
            groups, ungrouped = await crud.get_full_tree(session, user.id)
            cards.append(
                {
                    "user": user,
                    "group_count": len(groups),
                    "plant_count": plant_count(groups, ungrouped),
                    "last_activity": last_activity.get(user.id),
                }
            )
    return templates.TemplateResponse(
        request, "users.html", {"cards": cards}
    )


@router.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int, _: str = Depends(require_auth)):
    async with get_session() as session:
        user = await get_user_or_404(session, user_id)
        groups, ungrouped = await crud.get_full_tree(session, user.id)
        ungrouped_label = await plant_service.get_ungrouped_label(session, user.id)
        ai_logs = await crud.list_ai_logs_for_user(session, user.id, limit=30)
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    count = plant_count(groups, ungrouped)
    return templates.TemplateResponse(
        request,
        "user_detail.html",
        {
            "user": user,
            "groups": groups,
            "ungrouped": ungrouped,
            "ungrouped_label": ungrouped_label,
            "plant_count": count,
            "ai_logs": ai_logs,
            "msg": msg,
            "err": err,
        },
    )


@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, _: str = Depends(require_auth)):
    """Очищает всю базу растений пользователя (группы и растения), но
    саму запись пользователя не трогает — бот должен продолжать узнавать
    его при следующем обращении, просто с пустым списком."""
    async with get_session() as session:
        await get_user_or_404(session, user_id)
        await crud.clear_user_plants(session, user_id)
        await session.commit()
    return redirect_with(f"/users/{user_id}", msg="База очищена")


@router.get("/users/{user_id}/export.csv")
async def export_csv(user_id: int, _: str = Depends(require_auth)):
    """Скачать список растений пользователя в CSV (group,name,comment)."""
    async with get_session() as session:
        user = await get_user_or_404(session, user_id)
        groups, ungrouped = await crud.get_full_tree(session, user.id)

    buf = io.StringIO()
    writer = csv_mod.writer(buf)
    writer.writerow(["group", "name", "comment"])
    for group in groups:
        for plant in group.plants:
            writer.writerow([group.name, plant.name, plant.comment or ""])
    for plant in ungrouped:
        writer.writerow(["", plant.name, plant.comment or ""])

    filename = f"plants_user{user_id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/users/{user_id}/import")
async def import_plants(
    user_id: int,
    _: str = Depends(require_auth),
    file: UploadFile | None = File(None),
    text: str = Form(""),
):
    """Импорт CSV или markdown-текста без промежуточного превью.
    Принимает либо загружённый CSV-файл, либо текст в поле textarea.
    Растения добавляются к существующим (не заменяют их)."""
    raw: str | None = None

    if file and file.filename:
        raw_bytes = await file.read()
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw_bytes.decode("cp1251", errors="replace")

    elif text.strip():
        raw = text.strip()

    if not raw:
        return redirect_with(f"/users/{user_id}", err="Нет данных для импорта")

    first_line = raw.splitlines()[0].lower().strip()
    looks_like_csv = "name" in first_line and ("," in first_line or ";" in first_line)

    try:
        rows = import_service.parse_csv(raw)
    except import_service.ImportParseError as exc:
        # Если заголовок явно похож на CSV, ошибку парсинга показываем как
        # есть — так понятнее, что не так с самим CSV, вместо попытки
        # угадать markdown-формат там, где его точно нет.
        if looks_like_csv:
            return redirect_with(f"/users/{user_id}", err=str(exc))
        try:
            rows = import_service.parse_markdown(raw)
        except import_service.ImportParseError as exc2:
            return redirect_with(f"/users/{user_id}", err=str(exc2))

    async with get_session() as session:
        user = await get_user_or_404(session, user_id)
        preview = await import_service.build_preview(session, user.id, rows)
        count, skipped = await import_service.commit_import(session, user.id, preview)

    msg = f"Импортировано {count} растений"
    if skipped:
        msg += f", пропущено {skipped} дублей"
    return redirect_with(f"/users/{user_id}", msg=msg)


@router.post("/users/{user_id}/ungrouped-label")
async def set_ungrouped_label(user_id: int, label: str = Form(""), _: str = Depends(require_auth)):
    """Пустая строка сбрасывает подпись обратно на дефолт "Без группы" —
    это НЕ создаёт настоящую группу, просто меняет текст в боте."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user:
            await crud.set_ungrouped_label(session, user, label)
            await session.commit()
    return user_redirect(user_id, "ungrouped")
