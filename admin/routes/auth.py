import math

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse

from admin.auth import (
    SESSION_COOKIE,
    client_ip,
    create_session_token,
    login_lockout_remaining,
    register_failed_login,
    reset_login_attempts,
    verify_credentials,
)
from admin.templating import templates

router = APIRouter()


def _format_wait(seconds: float) -> str:
    minutes = math.ceil(seconds / 60)
    if minutes <= 1:
        return f"{max(1, math.ceil(seconds))} сек."
    return f"{minutes} мин."


@router.get("/login")
async def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    ip = client_ip(request)

    remaining = login_lockout_remaining(ip)
    if remaining > 0:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next,
                "error": f"Слишком много неудачных попыток. Попробуйте снова через {_format_wait(remaining)}.",
            },
            status_code=429,
        )

    if not verify_credentials(username, password):
        register_failed_login(ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    reset_login_attempts(ip)
    token = create_session_token(username)
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, max_age=60 * 60 * 24 * 7, samesite="lax"
    )
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
