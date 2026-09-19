from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from admin.database import get_session
from bot.db import crud
from bot.db.models import Group, Plant, WateringZone
from bot.services import watering_service


def group_anchor(group_id: int | None) -> str:
    """id секции на странице пользователя, к которой относится группа —
    используется, чтобы после удаления/переименования/добавления вернуть
    админа туда же, а не наверх страницы."""
    return f"group-{group_id}" if group_id is not None else "ungrouped"


def user_redirect(user_id: int, anchor: str | None = None) -> RedirectResponse:
    url = f"/users/{user_id}#{anchor}" if anchor else f"/users/{user_id}"
    return RedirectResponse(url, status_code=303)


def redirect_with(url: str, status_code: int = 303, **params) -> RedirectResponse:
    """RedirectResponse с query-параметрами, безопасно закодированными через
    urlencode — в отличие от f"{url}?msg={msg}", не ломается, если значение
    когда-нибудь будет содержать &, # или %. None-значения пропускаются,
    чтобы не плодить msg=None/err=None в адресной строке."""
    query = {key: value for key, value in params.items() if value is not None}
    if query:
        url = f"{url}?{urlencode(query)}"
    return RedirectResponse(url, status_code=status_code)


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


def zone_redirect(user_id: int, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    """Возврат на страницу пользователя к блоку «Зоны полива» с flash-
    сообщением. redirect_with тут не подходит: он добавляет query после
    всего URL, а якорь #watering должен стоять после query-строки."""
    query = {key: value for key, value in (("msg", msg), ("err", err)) if value is not None}
    url = f"/users/{user_id}"
    if query:
        url += f"?{urlencode(query)}"
    return RedirectResponse(f"{url}#watering", status_code=303)


def zone_view(zone: WateringZone, now) -> dict:
    """Всё, что нужно шаблону про одну зону, посчитанное в одном месте
    (шаблоны не должны знать про notified_at/next_watering_at).

    state:
      waiting — срок ещё не наступил;
      sent    — срок наступил, напоминание по этому циклу уже отправлено;
      queued  — срок наступил, а напоминания ещё не было (бот выключен или
                Telegram временно отвечал ошибкой — планировщик повторит
                на ближайшей минутной проверке).
    Та же логика, что в crud.list_due_zones, только для отображения."""
    due = watering_service.is_due(zone, now)
    reminded = zone.notified_at is not None and zone.notified_at >= zone.next_watering_at
    if not due:
        state = "waiting"
    elif reminded:
        state = "sent"
    else:
        state = "queued"
    return {
        "zone": zone,
        "state": state,
        "next_text": watering_service.describe_due(zone.next_watering_at, now),
        "last_text": watering_service.describe_last_watered(zone.last_watered_at, now),
        "notify_text": watering_service.describe_notify_time(zone.notify_time),
    }
