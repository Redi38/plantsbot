from datetime import time

from fastapi import APIRouter, Depends, Form
from fastapi.requests import Request

from admin.auth import require_auth
from admin.database import get_session
from admin.helpers import get_user_or_404, zone_redirect, zone_view
from admin.templating import templates
from bot.db import crud
from bot.services import watering_service as ws

router = APIRouter()

FILTER_DUE = "due"


def _interval_error() -> str:
    return f"Интервал должен быть от {ws.MIN_INTERVAL_DAYS} до {ws.MAX_INTERVAL_DAYS} дней"


def _interval_ok(days: int) -> bool:
    return ws.MIN_INTERVAL_DAYS <= days <= ws.MAX_INTERVAL_DAYS


def _parse_notify_time(raw: str | None) -> time | None:
    """Значение из <input type="time">: пустая строка — фиксированного часа
    нет, иначе браузер уже прислал корректный «ЧЧ:ММ»."""
    if not raw:
        return None
    return time.fromisoformat(raw)


@router.get("/watering")
async def watering_overview(
    request: Request,
    status: str | None = None,
    _: str = Depends(require_auth),
):
    """Зоны полива всех пользователей одним списком — сначала те, кому
    полив нужен раньше всех. Фильтр «Пора поливать» показывает зоны, у
    которых срок уже наступил (напоминание отправлено или ждёт отправки).
    Управление зоной — на странице пользователя, сюда ведёт ссылка."""
    now = ws.utcnow()
    status_filter = status if status == FILTER_DUE else None

    async with get_session() as session:
        rows = await crud.list_all_zones(session)

    items = [{"user": user, **zone_view(zone, now)} for zone, user in rows]
    due_count = sum(1 for item in items if item["state"] != "waiting")
    if status_filter == FILTER_DUE:
        items = [item for item in items if item["state"] != "waiting"]

    return templates.TemplateResponse(
        request,
        "watering.html",
        {
            "items": items,
            "total": len(rows),
            "due_count": due_count,
            "status_filter": status_filter,
            "filter_due": FILTER_DUE,
        },
    )


@router.post("/users/{user_id}/zones")
async def create_zone(
    user_id: int,
    name: str = Form(...),
    interval_days: int = Form(...),
    notify_time: str = Form(""),
    _: str = Depends(require_auth),
):
    if not _interval_ok(interval_days):
        return zone_redirect(user_id, err=_interval_error())
    async with get_session() as session:
        await get_user_or_404(session, user_id)
        try:
            zone = await ws.add_zone(session, user_id, name, interval_days, _parse_notify_time(notify_time))
        except ws.ZoneAlreadyExists:
            return zone_redirect(user_id, err=f"Зона «{name.strip()}» уже есть")
        except ValueError:
            return zone_redirect(user_id, err=f"Название зоны — от 1 до {ws.MAX_NAME_LENGTH} символов")
    return zone_redirect(user_id, msg=f"Зона «{zone.name}» добавлена")


@router.post("/zones/{zone_id}/update")
async def update_zone(
    zone_id: int,
    user_id: int = Form(...),
    name: str = Form(...),
    interval_days: int = Form(...),
    notify_time: str = Form(""),
    _: str = Depends(require_auth),
):
    """Меняет название, интервал и/или время напоминания. Интервал в
    set_interval сбрасывает отсчёт до следующего полива от текущего
    момента — это осознанное поведение бота, поэтому вызываем его только
    если интервал реально поменялся, а не при каждом сохранении формы.
    Время напоминания меняем через set_notify_time отдельно: оно не
    трогает день следующего полива, только час."""
    if not _interval_ok(interval_days):
        return zone_redirect(user_id, err=_interval_error())
    new_notify_time = _parse_notify_time(notify_time)
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return zone_redirect(user_id, err="Зона не найдена")
        changed = False
        try:
            if name.strip() != zone.name:
                await ws.rename(session, zone, name)
                changed = True
        except ws.ZoneAlreadyExists:
            return zone_redirect(user_id, err=f"Зона «{name.strip()}» уже есть")
        except ValueError:
            return zone_redirect(user_id, err=f"Название зоны — от 1 до {ws.MAX_NAME_LENGTH} символов")
        if interval_days != zone.interval_days:
            await ws.set_interval(session, zone, interval_days)
            changed = True
        if new_notify_time != zone.notify_time:
            await ws.set_notify_time(session, zone, new_notify_time)
            changed = True
    return zone_redirect(user_id, msg="Сохранено" if changed else "Без изменений")


@router.post("/zones/{zone_id}/watered")
async def zone_watered(zone_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return zone_redirect(user_id, err="Зона не найдена")
        await ws.mark_watered(session, zone)
    return zone_redirect(user_id, msg=f"Зона «{zone.name}» отмечена политой")


@router.post("/zones/{zone_id}/snooze")
async def zone_snooze(
    zone_id: int,
    user_id: int = Form(...),
    days: int = Form(...),
    _: str = Depends(require_auth),
):
    if not _interval_ok(days):
        return zone_redirect(user_id, err=_interval_error())
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return zone_redirect(user_id, err="Зона не найдена")
        await ws.snooze(session, zone, days)
    return zone_redirect(user_id, msg=f"Полив зоны «{zone.name}» отложен на {days} дн.")


@router.post("/zones/{zone_id}/delete")
async def delete_zone(zone_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        zone = await crud.get_zone(session, zone_id, user_id)
        if zone is None:
            return zone_redirect(user_id, err="Зона не найдена")
        await ws.remove(session, zone)
    return zone_redirect(user_id, msg=f"Зона «{zone.name}» удалена")
