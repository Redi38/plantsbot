import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/plants.db")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """На случай, если админка стартует раньше бота — таблицы должны существовать.
    Миграция колонок здесь тоже нужна: если бот и админка запускаются как разные
    контейнеры, любой из них может оказаться первым, кто увидит старую БД."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_add_missing_columns(conn)


async def _migrate_add_missing_columns(conn) -> None:
    result = await conn.exec_driver_sql("PRAGMA table_info(users)")
    existing_columns = {row[1] for row in result.fetchall()}

    if "username" not in existing_columns:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN username VARCHAR(64)")
    if "full_name" not in existing_columns:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN full_name VARCHAR(128)")
    if "ungrouped_label" not in existing_columns:
        await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN ungrouped_label VARCHAR(100)")


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
