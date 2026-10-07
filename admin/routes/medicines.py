from fastapi import APIRouter, Depends, Form
from fastapi.requests import Request

from admin.auth import require_auth
from admin.database import get_session
from admin.helpers import get_user_or_404, medicine_redirect, medicine_view
from admin.templating import templates
from bot.db import crud
from bot.services import medicine_service as ms

router = APIRouter()

FILTER_EXPIRED = "expired"
FILTER_SOON = "soon"
_FILTER_STATES = {FILTER_EXPIRED: {"expired"}, FILTER_SOON: {"soon"}}

EXPIRY_ERROR = "Срок годности: «05.2027» (до конца месяца) или «31.05.2027»"


@router.get("/medicines")
async def medicines_overview(
    request: Request,
    status: str | None = None,
    _: str = Depends(require_auth),
):
    """Препараты всех пользователей одним списком — сначала те, у кого срок
    годности раньше. Фильтры: «Срок вышел» и «Скоро закончится». Управление
    препаратом — на странице пользователя, сюда ведёт ссылка."""
    today = ms.today_utc()
    status_filter = status if status in _FILTER_STATES else None

    async with get_session() as session:
        rows = await crud.list_all_medicines(session)

    items = [{"user": user, **medicine_view(medicine, today)} for medicine, user in rows]
    expired_count = sum(1 for item in items if item["state"] == "expired")
    soon_count = sum(1 for item in items if item["state"] == "soon")
    if status_filter:
        items = [item for item in items if item["state"] in _FILTER_STATES[status_filter]]

    return templates.TemplateResponse(
        request,
        "medicines.html",
        {
            "items": items,
            "total": len(rows),
            "expired_count": expired_count,
            "soon_count": soon_count,
            "status_filter": status_filter,
            "filter_expired": FILTER_EXPIRED,
            "filter_soon": FILTER_SOON,
        },
    )


def _parse_expiry(raw: str):
    """(ok, date|None): пустое поле — срока нет (это нормально), непустое
    должно разобраться так же, как в боте."""
    raw = raw.strip()
    if not raw:
        return True, None
    value = ms.parse_expiry(raw)
    return value is not None, value


@router.post("/users/{user_id}/medicines")
async def create_medicine(
    user_id: int,
    name: str = Form(...),
    kind: str = Form(...),
    active_substance: str = Form(""),
    expires_at: str = Form(""),
    comment: str = Form(""),
    _: str = Depends(require_auth),
):
    ok, expiry = _parse_expiry(expires_at)
    if not ok:
        return medicine_redirect(user_id, err=EXPIRY_ERROR)
    async with get_session() as session:
        await get_user_or_404(session, user_id)
        try:
            medicine = await ms.add_medicine(session, user_id, name, kind, active_substance, expiry, comment)
        except ms.TooManyMedicines:
            return medicine_redirect(user_id, err=f"В аптечке уже {ms.MAX_MEDICINES} препаратов — это максимум")
        except ValueError:
            return medicine_redirect(
                user_id,
                err=(
                    f"Название — до {ms.MAX_NAME_LENGTH}, тип — до {ms.MAX_KIND_LENGTH}, "
                    f"вещество — до {ms.MAX_SUBSTANCE_LENGTH}, комментарий — до {ms.MAX_COMMENT_LENGTH} символов"
                ),
            )
    return medicine_redirect(user_id, msg=f"Препарат «{medicine.name}» добавлен")


@router.post("/medicines/{medicine_id}/update")
async def update_medicine(
    medicine_id: int,
    user_id: int = Form(...),
    name: str = Form(...),
    kind: str = Form(...),
    active_substance: str = Form(""),
    expires_at: str = Form(""),
    comment: str = Form(""),
    _: str = Depends(require_auth),
):
    ok, expiry = _parse_expiry(expires_at)
    if not ok:
        return medicine_redirect(user_id, err=EXPIRY_ERROR)
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
        if medicine is None:
            return medicine_redirect(user_id, err="Препарат не найден")
        try:
            changed = await ms.edit_medicine(session, medicine, name, kind, active_substance, expiry, comment)
        except ValueError:
            return medicine_redirect(
                user_id,
                err=(
                    f"Название — до {ms.MAX_NAME_LENGTH}, тип — до {ms.MAX_KIND_LENGTH}, "
                    f"вещество — до {ms.MAX_SUBSTANCE_LENGTH}, комментарий — до {ms.MAX_COMMENT_LENGTH} символов; "
                    "название и тип не могут быть пустыми"
                ),
            )
    return medicine_redirect(user_id, msg="Сохранено" if changed else "Без изменений")


@router.post("/medicines/{medicine_id}/delete")
async def delete_medicine(medicine_id: int, user_id: int = Form(...), _: str = Depends(require_auth)):
    async with get_session() as session:
        medicine = await crud.get_medicine(session, medicine_id, user_id)
        if medicine is None:
            return medicine_redirect(user_id, err="Препарат не найден")
        name = medicine.name
        await ms.remove(session, medicine)
    return medicine_redirect(user_id, msg=f"Препарат «{name}» удалён")
