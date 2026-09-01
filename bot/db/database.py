from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import config
from bot.db.models import Base

engine = create_async_engine(config.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Для SQLite этого достаточно —
    Alembic имеет смысл подключать, только если ожидаются частые миграции схемы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # включаем WAL, чтобы чтение не блокировалось во время записи
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
