from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from admin.auth import require_auth
from admin.database import get_session, init_db
from bot.db import crud

app = FastAPI(title="Учёт растений — админка")
templates = Jinja2Templates(directory="admin/templates")


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


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


# ---------- Растения и группы одного пользователя ----------

@app.get("/users/{user_id}")
async def user_detail(request: Request, user_id: int, _: str = Depends(require_auth)):
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        groups, ungrouped = await crud.get_full_tree(session, user.id)
    return templates.TemplateResponse(
        "user_detail.html",
        {"request": request, "user": user, "groups": groups, "ungrouped": ungrouped},
    )


@app.post("/users/{user_id}/groups")
async def create_group(user_id: int, name: str = Form(...), _: str = Depends(require_auth)):
    if name.strip():
        async with get_session() as session:
            await crud.create_group(session, user_id, name)
            await session.commit()
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/users/{user_id}/plants")
async def create_plant(
    user_id: int,
    name: str = Form(...),
    comment: str = Form(""),
    group_id: str = Form(""),
    _: str = Depends(require_auth),
):
    if not name.strip():
        return RedirectResponse(f"/users/{user_id}", status_code=303)
    async with get_session() as session:
        gid = int(group_id) if group_id else None
        await crud.create_plant(
            session, user_id, name, group_id=gid, comment=comment.strip() or None
        )
        await session.commit()
    return RedirectResponse(f"/users/{user_id}", status_code=303)


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
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/groups/{group_id}/delete")
async def delete_group(group_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        group = await crud.get_group(session, group_id, user_id)
        if group:
            await crud.delete_group(session, group)
            await session.commit()
    return RedirectResponse(f"/users/{user_id}", status_code=303)


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
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        if plant and name.strip():
            gid = int(group_id) if group_id else None
            await crud.update_plant(
                session, plant, name=name, comment=comment.strip() or None, group_id=gid
            )
            await session.commit()
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@app.post("/plants/{plant_id}/delete")
async def delete_plant(plant_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        if plant:
            await crud.delete_plant(session, plant)
            await session.commit()
    return RedirectResponse(f"/users/{user_id}", status_code=303)
