from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse

from admin.auth import SESSION_COOKIE, create_session_token, verify_credentials
from admin.templating import templates

router = APIRouter()


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
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next, "error": "Неверный логин или пароль"},
            status_code=401,
        )

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
