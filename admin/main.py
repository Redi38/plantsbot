from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from admin.auth import AuthRequired
from admin.database import init_db
from admin.routes import ai_logs, auth, groups, plants, users

app = FastAPI(title="PlantsBot Dashboard")
app.mount("/static", StaticFiles(directory="admin/static"), name="static")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(ai_logs.router)
app.include_router(groups.router)
app.include_router(plants.router)


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
