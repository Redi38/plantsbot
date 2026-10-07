"""Админка: аптечка (страница /medicines, карточка на странице пользователя,
добавление/изменение/удаление). Сквозные проверки через TestClient на
временной SQLite-базе."""

import os
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from admin import database as admin_db
from admin.auth import require_auth
from admin.helpers import medicine_view
from admin.main import app
from bot.db import crud
from bot.db.models import Base, Medicine
from bot.services import medicine_service as ms

TODAY = date(2026, 10, 7)


def _med(expires_at: date | None) -> Medicine:
    return Medicine(user_id=1, name="X", kind="Инсектицид", expires_at=expires_at)


@pytest.mark.parametrize(
    ("expires_at", "state"),
    [
        (None, "no_date"),
        (date(2026, 10, 6), "expired"),
        (date(2026, 10, 7), "soon"),
        (date(2026, 11, 6), "soon"),
        (date(2026, 11, 7), "ok"),
    ],
)
def test_medicine_view_state(expires_at, state):
    assert medicine_view(_med(expires_at), TODAY)["state"] == state


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(admin_db, "async_session", sessionmaker)
    app.dependency_overrides[require_auth] = lambda: "admin"
    # Без `with` — startup (init_db на реальном пути) не запускается.
    test_client = TestClient(app, follow_redirects=False)
    test_client.sessionmaker = sessionmaker  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()
    await engine.dispose()


async def _make_user(client) -> int:
    async with client.sessionmaker() as session:
        user = await crud.get_or_create_user(session, 111, "tester", "Test")
        await session.commit()
        return user.id


async def _all(client) -> list[Medicine]:
    async with client.sessionmaker() as session:
        return [m for m, _ in await crud.list_all_medicines(session)]


async def test_create_with_all_fields(client):
    uid = await _make_user(client)
    resp = client.post(
        f"/users/{uid}/medicines",
        data={"name": "Актара", "kind": "Инсектицид", "active_substance": "тиаметоксам", "expires_at": "05.2099", "comment": "от тли"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("#medicines")
    (m,) = await _all(client)
    assert (m.name, m.kind, m.active_substance, m.expires_at, m.comment) == (
        "Актара", "Инсектицид", "тиаметоксам", date(2099, 5, 31), "от тли",
    )


async def test_create_only_name_and_kind(client):
    uid = await _make_user(client)
    client.post(f"/users/{uid}/medicines", data={"name": "Фундазол", "kind": "Фунгицид"})
    (m,) = await _all(client)
    assert m.expires_at is None and m.active_substance is None and m.comment is None


async def test_create_bad_expiry_is_rejected(client):
    uid = await _make_user(client)
    resp = client.post(f"/users/{uid}/medicines", data={"name": "A", "kind": "B", "expires_at": "скоро"})
    assert "err=" in resp.headers["location"]
    assert await _all(client) == []


async def test_create_for_missing_user_is_404(client):
    resp = client.post("/users/999/medicines", data={"name": "A", "kind": "B"})
    assert resp.status_code == 404


async def test_update_changes_fields_and_resets_reminder_on_new_expiry(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        m = await ms.add_medicine(session, uid, "Актара", "Инсектицид", expires_at=date(2026, 10, 20))
        m.notified_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        mid = m.id

    resp = client.post(
        f"/medicines/{mid}/update",
        data={"user_id": uid, "name": "Актара 25", "kind": "Инсектицид", "active_substance": "", "expires_at": "12.2099", "comment": ""},
    )
    assert "msg=" in resp.headers["location"]
    (m,) = await _all(client)
    assert m.name == "Актара 25"
    assert m.expires_at == date(2099, 12, 31)
    assert m.notified_at is None


async def test_update_same_expiry_keeps_reminder_flag(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        m = await ms.add_medicine(session, uid, "Актара", "Инсектицид", expires_at=date(2026, 10, 31))
        m.notified_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        mid = m.id

    client.post(
        f"/medicines/{mid}/update",
        data={"user_id": uid, "name": "Актара", "kind": "Инсектицид", "expires_at": "10.2026", "comment": "заметка"},
    )
    (m,) = await _all(client)
    assert m.comment == "заметка"
    assert m.notified_at is not None


async def test_update_wrong_owner_not_found(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        m = await ms.add_medicine(session, uid, "Актара", "Инсектицид")
        mid = m.id
    resp = client.post(f"/medicines/{mid}/update", data={"user_id": uid + 1, "name": "Z", "kind": "Z"})
    assert "err=" in resp.headers["location"]
    (m,) = await _all(client)
    assert m.name == "Актара"


async def test_update_empty_name_is_rejected(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        m = await ms.add_medicine(session, uid, "Актара", "Инсектицид")
        mid = m.id
    resp = client.post(f"/medicines/{mid}/update", data={"user_id": uid, "name": "  ", "kind": "Инсектицид"})
    assert "err=" in resp.headers["location"]
    (m,) = await _all(client)
    assert m.name == "Актара"


async def test_delete(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        m = await ms.add_medicine(session, uid, "Актара", "Инсектицид")
        mid = m.id
    resp = client.post(f"/medicines/{mid}/delete", data={"user_id": uid})
    assert "msg=" in resp.headers["location"]
    assert await _all(client) == []


async def test_overview_page_and_filters(client):
    uid = await _make_user(client)
    today = ms.today_utc()
    async with client.sessionmaker() as session:
        await ms.add_medicine(session, uid, "Старый", "Фунгицид", expires_at=today - timedelta(days=5))
        await ms.add_medicine(session, uid, "Скоро", "Фунгицид", expires_at=today + timedelta(days=10))
        await ms.add_medicine(session, uid, "Свежий", "Фунгицид", expires_at=today + timedelta(days=400))

    page = client.get("/medicines")
    assert page.status_code == 200
    assert all(name in page.text for name in ("Старый", "Скоро", "Свежий"))

    expired = client.get("/medicines?status=expired").text
    assert "Старый" in expired and "Скоро</span>" not in expired and "Свежий" not in expired

    soon = client.get("/medicines?status=soon").text
    assert "Скоро</span>" in soon and "Старый" not in soon


async def test_user_page_shows_medicines_card(client):
    uid = await _make_user(client)
    async with client.sessionmaker() as session:
        await ms.add_medicine(session, uid, "Актара", "Инсектицид", expires_at=date(2099, 5, 31))
    page = client.get(f"/users/{uid}")
    assert page.status_code == 200
    assert 'id="medicines"' in page.text
    assert "Актара" in page.text
    assert "05.2099" in page.text
