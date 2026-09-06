from fastapi import APIRouter, Depends, Form

from admin.auth import require_auth
from admin.database import get_session
from admin.helpers import group_anchor, user_redirect, with_plant
from bot.db import crud

router = APIRouter()


@router.post("/users/{user_id}/plants")
async def create_plant(
    user_id: int,
    name: str = Form(...),
    comment: str = Form(""),
    group_id: str = Form(""),
    _: str = Depends(require_auth),
):
    gid = int(group_id) if group_id else None
    if not name.strip():
        return user_redirect(user_id, group_anchor(gid))
    async with get_session() as session:
        await crud.create_plant(
            session, user_id, name, group_id=gid, comment=comment.strip() or None
        )
        await session.commit()
    return user_redirect(user_id, group_anchor(gid))


@router.post("/plants/{plant_id}/rename")
async def rename_plant(
    plant_id: int,
    user_id: int = Form(...),
    name: str = Form(...),
    comment: str = Form(""),
    group_id: str = Form(""),
    _: str = Depends(require_auth),
):
    gid = int(group_id) if group_id else None
    if name.strip():
        await with_plant(
            user_id,
            plant_id,
            lambda session, plant: crud.update_plant(
                session, plant, name=name, comment=comment.strip() or None, group_id=gid
            ),
        )
    return user_redirect(user_id, group_anchor(gid))


@router.post("/plants/{plant_id}/delete")
async def delete_plant(plant_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    plant = await with_plant(user_id, plant_id, crud.delete_plant)
    return user_redirect(user_id, group_anchor(plant.group_id) if plant else None)
