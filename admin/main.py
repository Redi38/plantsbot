from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.requests import Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from admin.auth import AuthRequired, create_session_token, require_auth, verify_credentials, SESSION_COOKIE
from admin.database import get_session, init_db
from bot.db import crud
from bot.services import import_service, plant_service

app = FastAPI(title="PlantsBot Dashboard")
app.mount("/static", StaticFiles(directory="admin/static"), name="static")
templates = Jinja2Templates(directory="admin/templates")


def _group_anchor(group_id: int | None) -> str:
    """id секции на странице пользователя, к которой относится группа —
    используется, чтобы после удаления/переименования/добавления вернуть
    админа туда же, а не наверх страницы."""
    return f"group-{group_id}" if group_id is not None else "ungrouped"


def _user_redirect(user_id: int, anchor: str | None = None) -> RedirectResponse:
    url = f"/users/{user_id}#{anchor}" if anchor else f"/users/{user_id}"
    return RedirectResponse(url, status_code=303)


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


# ---------- Вход / выход ----------

@app.get("/login")
async def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    token = create_session_token(username)
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax"
    )
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------- Пользователи ----------

@app.get("/")
async def users_list(request: Request, _: str = Depends(require_auth)):
    async with get_session() as session:
        users = await crud.list_users(session)
        cards = []
        for user in users:
            groups, ungrouped = await crud.get_full_tree(session, user.id)
            plant_count = sum(len(g.plants) for g in groups) + len(ungrouped)
            cards.append({"user": user, "group_count": len(groups), "plant_count": plant_count})
    return templates.TemplateResponse(
        "users.html", {"request": request, "cards": cards}
    )


# ---------- Логи ИИ-агента ----------

@app.get("/ai-logs")
async def ai_logs_list(request: Request, _: str = Depends(require_auth)):
    """Последние обращения к ИИ-агенту по всем пользователям — чтобы видеть
    реальные формулировки и промахи распознавания вне контекста конкретного
    пользователя."""
    async with get_session() as session:
        entries = await crud.list_ai_logs_all(session, limit=200)
    return templates.TemplateResponse(
        "ai_logs.html", {"request": request, "entries": entries}
    )


# ---------- Растения и группы одного пользователя ----------

@app.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int, _: str = Depends(require_auth)):
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        groups, ungrouped = await crud.get_full_tree(session, user.id)
        ungrouped_label = await plant_service.get_ungrouped_label(session, user.id)
        ai_logs = await crud.list_ai_logs_for_user(session, user.id, limit=30)
    msg = request.query_params.get("msg")
    err = request.query_params.get("err")
    plant_count = sum(len(g.plants) for g in groups) + len(ungrouped)
    return templates.TemplateResponse(
        "user_detail.html",
        {
            "request": request,
            "user": user,
            "groups": groups,
            "ungrouped": ungrouped,
            "ungrouped_label": ungrouped_label,
            "plant_count": plant_count,
            "ai_logs": ai_logs,
            "msg": msg,
            "err": err,
        },
    )


@app.post("/users/{user_id}/delete")
async def delete_user(user_id: int, _: str = Depends(require_auth)):
    """Полное и безвозвратное удаление пользователя со всеми его группами
    и растениями."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        await crud.delete_user_data(session, user_id)
        await session.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/users/{user_id}/export.csv")
async def export_csv(user_id: int, _: str = Depends(require_auth)):
    """Скачать список растений пользователя в CSV (group,name,comment)."""
    import io, csv as csv_mod
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
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


@app.post("/users/{user_id}/import")
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
        return RedirectResponse(f"/users/{user_id}?err=Нет данных для импорта", status_code=303)

    # Пробуем определить формат: CSV если есть заголовок group,name,comment или name,
    # иначе считаем markdown
    first_line = raw.splitlines()[0].lower().strip()
    is_csv = "name" in first_line and ("," in first_line or ";" in first_line)

    try:
        if is_csv:
            rows = import_service.parse_csv(raw)
        else:
            # для markdown-формата пробуем сначала CSV (вдруг без заголовка не подходит),
            # fallback на markdown
            try:
                rows = import_service.parse_csv(raw)
            except import_service.ImportParseError:
                rows = import_service.parse_markdown(raw)
    except import_service.ImportParseError as exc:
        return RedirectResponse(f"/users/{user_id}?err={exc}", status_code=303)

    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        preview = await import_service.build_preview(session, user.id, rows)
        count = await import_service.commit_import(session, user.id, preview)

    return RedirectResponse(f"/users/{user_id}?msg=Импортировано {count} растений", status_code=303)


@app.post("/users/{user_id}/groups")
async def create_group(user_id: int, name: str = Form(...), _: str = Depends(require_auth)):
    anchor = "groups"
    if name.strip():
        async with get_session() as session:
            group = await crud.create_group(session, user_id, name)
            await session.commit()
            anchor = _group_anchor(group.id)
    return _user_redirect(user_id, anchor)


@app.post("/users/{user_id}/plants")
async def create_plant(
    user_id: int,
    name: str = Form(...),
    comment: str = Form(""),
    group_id: str = Form(""),
    _: str = Depends(require_auth),
):
    gid = int(group_id) if group_id else None
    if not name.strip():
        return _user_redirect(user_id, _group_anchor(gid))
    async with get_session() as session:
        await crud.create_plant(
            session, user_id, name, group_id=gid, comment=comment.strip() or None
        )
        await session.commit()
    return _user_redirect(user_id, _group_anchor(gid))


# ---------- "Без группы" (виртуальная группа, только подпись) ----------

@app.post("/users/{user_id}/ungrouped-label")
async def set_ungrouped_label(user_id: int, label: str = Form(""), _: str = Depends(require_auth)):
    """Пустая строка сбрасывает подпись обратно на дефолт "Без группы" —
    это НЕ создаёт настоящую группу, просто меняет текст в боте."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user:
            await crud.set_ungrouped_label(session, user, label)
            await session.commit()
    return _user_redirect(user_id, "ungrouped")


# ---------- Группы ----------

@app.post("/groups/{group_id}/rename")
async def rename_group(
    group_id: int, user_id: int = Form(...), name: str = Form(...), _: str = Depends(require_auth)
):
    async with get_session() as session:
        group = await crud.get_group(session, group_id, user_id)
        if group and name.strip():
            await crud.rename_group(session, group, name)
            await session.commit()
    return _user_redirect(user_id, _group_anchor(group_id))


@app.post("/groups/{group_id}/delete")
async def delete_group(group_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        group = await crud.get_group(session, group_id, user_id)
        if group:
            await crud.delete_group(session, group)
            await session.commit()
    # группы больше нет, но её растения переехали в "без группы" —
    # логично показать именно этот раздел, а не верх страницы
    return _user_redirect(user_id, "ungrouped")


@app.post("/groups/{group_id}/delete-with-plants")
async def delete_group_with_plants(group_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    """Удаляет группу вместе со всеми растениями внутри неё (в отличие от
    /groups/{group_id}/delete, где растения остаются, просто без группы)."""
    async with get_session() as session:
        group = await crud.get_group(session, group_id, user_id)
        if group:
            await crud.delete_group_with_plants(session, group)
            await session.commit()
    return _user_redirect(user_id, "groups")


# ---------- Растения ----------

@app.post("/plants/{plant_id}/rename")
async def rename_plant(
    plant_id: int,
    user_id: int = Form(...),
    name: str = Form(...),
    comment: str = Form(""),
    group_id: str = Form(""),
    _: str = Depends(require_auth),
):
    gid = int(group_id) if group_id else None
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        if plant and name.strip():
            await crud.update_plant(
                session, plant, name=name, comment=comment.strip() or None, group_id=gid
            )
            await session.commit()
    # растение могло переехать в другую группу — якорим на новую, не старую
    return _user_redirect(user_id, _group_anchor(gid))


@app.post("/plants/{plant_id}/delete")
async def delete_plant(plant_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        anchor = _group_anchor(plant.group_id) if plant else None
        if plant:
            await crud.delete_plant(session, plant)
            await session.commit()
    return _user_redirect(user_id, anchor)
