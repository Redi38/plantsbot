from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Group, Plant
from bot.utils.fuzzy import fuzzy_find


async def get_group_by_name(session: AsyncSession, user_id: int, name: str) -> Group | None:
    """Регистронезависимый поиск группы с обрезкой пробелов —
    чтобы "Суккуленты" и "суккуленты " матчились в одну группу.

    Сравнение через func.lower() в SQL здесь не подходит: встроенный
    LOWER() в SQLite приводит к нижнему регистру только ASCII-символы
    (без расширения ICU кириллица не трогается вообще), а бот целиком
    русскоязычный. Поэтому регистронезависимость считается в Python,
    где str.lower() работает с Unicode корректно; группы одного
    пользователя — это единицы-десятки записей, так что подгрузка
    всех и сравнение в цикле не создаёт проблем с производительностью."""
    normalized = name.strip().lower()
    result = await session.execute(select(Group).where(Group.user_id == user_id))
    for group in result.scalars():
        if group.name.strip().lower() == normalized:
            return group
    return None


async def find_groups_fuzzy(session: AsyncSession, user_id: int, name: str) -> list[Group]:
    """Нечёткий поиск групп (обёртка над bot.utils.fuzzy.fuzzy_find) — на
    случай, если пользователь написал имя группы не точь-в-точь (например
    «testik» вместо «testik1», либо наоборот с лишним словом/цифрой).
    Используется как fallback после того, как get_group_by_name не нашёл
    точного совпадения. Возвращает всех подходящих кандидатов — вызывающий
    код сам решает, что делать с несколькими совпадениями (обычно: \
    действовать только если ровно один кандидат, иначе просить уточнить)."""
    result = await session.execute(select(Group).where(Group.user_id == user_id))
    return fuzzy_find(list(result.scalars()), name)


async def create_group(session: AsyncSession, user_id: int, name: str) -> Group:
    group = Group(user_id=user_id, name=name.strip())
    session.add(group)
    await session.flush()
    return group


async def get_or_create_group(session: AsyncSession, user_id: int, name: str) -> tuple[Group, bool]:
    """Возвращает (группа, была_ли_создана_заново)."""
    existing = await get_group_by_name(session, user_id, name)
    if existing:
        return existing, False
    return await create_group(session, user_id, name), True


async def rename_group(session: AsyncSession, group: Group, new_name: str) -> None:
    group.name = new_name.strip()
    await session.flush()


async def delete_group(session: AsyncSession, group: Group) -> None:
    for plant in group.plants:
        plant.group_id = None
    await session.delete(group)
    await session.flush()


async def delete_group_with_plants(session: AsyncSession, group: Group) -> None:
    """Как delete_group, но удаляет и все растения внутри группы, а не
    просто открепляет их."""
    await session.execute(delete(Plant).where(Plant.group_id == group.id))
    await session.delete(group)
    await session.flush()


async def move_group_plants(session: AsyncSession, group: Group, target_group_id: int | None) -> None:
    """Переносит все растения группы в другую группу (или в "без группы"
    при target_group_id=None), саму группу не трогает.

    Намеренно bulk UPDATE, а не мутация ORM-объектов group.plants: если
    менять plant.group_id в цикле (или тем более group.plants.clear()),
    relationship/backref синхронизация на flush всё равно попытается
    привести дочернюю коллекцию в соответствие со своим представлением
    связи и обнулит group_id повторно — свежее прямое присвоение
    перезаписывается. Bulk UPDATE идёт в обход ORM identity map и такой
    проблемы не создаёт.

    После апдейта обязательно expire у group атрибут "plants": сама
    ORM-коллекция была загружена заранее (selectinload в get_group) и
    без expire останется устаревшей — со старым списком растений. Если
    следом вызвать session.delete(group) (см.
    group_service.remove_move_plants), SQLAlchemy на flush обратится к
    этой коллекции, чтобы решить, что делать с "детьми" удаляемого
    родителя, и увидев там (устаревшие) растения, обнулит им group_id
    ещё раз. expire заставляет SQLAlchemy перечитать коллекцию из БД —
    к этому моменту она уже пустая (растения перенесены), поэтому
    удаление группы больше их не трогает."""
    await session.execute(
        update(Plant).where(Plant.group_id == group.id).values(group_id=target_group_id)
    )
    session.expire(group, ["plants"])
    await session.flush()


async def get_group(session: AsyncSession, group_id: int, user_id: int) -> Group | None:
    result = await session.execute(
        select(Group)
        .where(Group.id == group_id, Group.user_id == user_id)
        .options(selectinload(Group.plants))
    )
    return result.scalar_one_or_none()


async def list_groups(session: AsyncSession, user_id: int) -> list[Group]:
    result = await session.execute(
        select(Group).where(Group.user_id == user_id).order_by(Group.name)
    )
    return list(result.scalars())
