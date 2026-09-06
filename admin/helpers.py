from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from admin.database import get_session
from bot.db import crud
from bot.db.models import Group, Plant


def group_anchor(group_id: int | None) -> str:
    """id секции на странице пользователя, к которой относится группа —
    используется, чтобы после удаления/переименования/добавления вернуть
    админа туда же, а не наверх страницы."""
    return f"group-{group_id}" if group_id is not None else "ungrouped"


def user_redirect(user_id: int, anchor: str | None = None) -> RedirectResponse:
    url = f"/users/{user_id}#{anchor}" if anchor else f"/users/{user_id}"
    return RedirectResponse(url, status_code=303)


async def with_group(user_id: int, group_id: int, mutate) -> Group | None:
    """Общий паттерн для мутаций группы из форм админки: открыть сессию,
    найти группу пользователя, если нашлась — вызвать mutate(session, group)
    и закоммитить. Возвращает саму группу (или None, если не найдена/чужая),
    чтобы вызывающий код мог решить, куда редиректить."""
    async with get_session() as session:
        group = await crud.get_group(session, group_id, user_id)
        if group:
            await mutate(session, group)
            await session.commit()
        return group


async def with_plant(user_id: int, plant_id: int, mutate) -> Plant | None:
    """Аналог with_group для растения."""
    async with get_session() as session:
        plant = await crud.get_plant(session, plant_id, user_id)
        if plant:
            await mutate(session, plant)
            await session.commit()
        return plant


async def get_user_or_404(session, user_id: int):
    """Общая проверка "пользователь существует" перед операциями в
    админке — раньше одна и та же пара строк повторялась в user_detail,
    delete_user, export_csv и import_plants."""
    user = await crud.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


def plant_count(groups: list[Group], ungrouped: list[Plant]) -> int:
    return sum(len(g.plants) for g in groups) + len(ungrouped)
