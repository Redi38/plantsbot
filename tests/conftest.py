"""Общие фикстуры для тестов.

BOT_TOKEN должен быть выставлен ДО первого импорта чего-либо из bot.* —
bot.config.load_config() читает переменные окружения на уровне модуля,
как только он импортируется (напрямую или транзитивно, например через
bot.services.ai_service, которому нужен AI_API_KEY при вызове реального
API — но не при импорте самого модуля)."""

import os

os.environ.setdefault("BOT_TOKEN", "test-token-for-pytest")

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db import crud
from bot.db.models import Base


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Изолированная in-memory SQLite база на каждый тест — не связана с
    глобальным engine из bot.db.database (тот целится в файл на диске по
    DATABASE_URL). Все функции в bot/services и bot/db/crud принимают
    сессию параметром, так что подменить источник для тестов легко, не
    трогая сам код бота.

    StaticPool держит одно и то же in-memory соединение на все запросы в
    рамках теста — без него каждое новое соединение видело бы уже другую,
    пустую базу."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s

    await engine.dispose()


@pytest_asyncio.fixture
async def user_id(session: AsyncSession) -> int:
    """Внутренний User.id (не telegram_id) — то, что реально принимают
    все функции в crud/services как user_id."""
    user = await crud.get_or_create_user(session, telegram_id=123456789, username="tester", full_name="Test User")
    await session.commit()
    return user.id
