from fastapi import APIRouter, Depends, Form

from admin.auth import require_auth
from admin.database import get_session
from admin.helpers import group_anchor, user_redirect, with_group
from bot.db import crud

router = APIRouter()


@router.post("/users/{user_id}/groups")
async def create_group(user_id: int, name: str = Form(...), _: str = Depends(require_auth)):
    anchor = "groups"
    if name.strip():
        async with get_session() as session:
            group = await crud.create_group(session, user_id, name)
            await session.commit()
            anchor = group_anchor(group.id)
    return user_redirect(user_id, anchor)


@router.post("/groups/{group_id}/rename")
async def rename_group(
    group_id: int, user_id: int = Form(...), name: str = Form(...), _: str = Depends(require_auth)
):
    if name.strip():
        await with_group(user_id, group_id, lambda session, group: crud.rename_group(session, group, name))
    return user_redirect(user_id, group_anchor(group_id))


@router.post("/groups/{group_id}/delete")
async def delete_group(group_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    await with_group(user_id, group_id, crud.delete_group)
    return user_redirect(user_id, "ungrouped")


@router.post("/groups/{group_id}/delete-with-plants")
async def delete_group_with_plants(group_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    """Удаляет группу вместе со всеми растениями внутри неё (в отличие от
    /groups/{group_id}/delete, где растения остаются, просто без группы)."""
    await with_group(user_id, group_id, crud.delete_group_with_plants)
    return user_redirect(user_id, "groups")
