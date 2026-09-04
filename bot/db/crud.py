from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import AiLog, Group, Plant, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        await session.flush()
    elif username is not None or full_name is not None:
        if username is not None:
            user.username = username
        if full_name is not None:
            user.full_name = full_name
        await session.flush()
    return user


async def set_ungrouped_label(session: AsyncSession, user: User, label: str | None) -> None:
    """label=None (или пустая строка после .strip()) сбрасывает подпись
    обратно на дефолт "Без группы" — сама "группа" при этом не хранится
    как запись, это просто подпись для растений без group_id."""
    user.ungrouped_label = label.strip() if label and label.strip() else None
    await session.flush()


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars())


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def clear_user_plants(session: AsyncSession, user_id: int) -> None:
    """Безвозвратно удаляет всю базу растений пользователя (группы и
    растения) вместе с кастомной подписью "без группы", но саму запись
    пользователя оставляет — чтобы бот продолжал узнавать его при
    следующем обращении. Удаляем явными bulk-запросами (а не через
    каскад на ORM-уровне), чтобы не зависеть от того, загружены ли
    связи — в асинхронной сессии ленивая подгрузка коллекций на flush
    недоступна. Используется только из админки."""
    await session.execute(delete(Plant).where(Plant.user_id == user_id))
    await session.execute(delete(Group).where(Group.user_id == user_id))
    await session.execute(
        update(User).where(User.id == user_id).values(ungrouped_label=None)
    )
    await session.flush()


# ---------- Группы ----------

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
    просто открепляет их. Используется только из админки — в самом боте
    удаление группы всегда сохраняет растения (переводит в "без группы")."""
    await session.execute(delete(Plant).where(Plant.group_id == group.id))
    await session.delete(group)
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


# ---------- Растения ----------

async def create_plant(
    session: AsyncSession,
    user_id: int,
    name: str,
    group_id: int | None = None,
    comment: str | None = None,
) -> Plant:
    plant = Plant(user_id=user_id, name=name.strip(), group_id=group_id, comment=comment)
    session.add(plant)
    await session.flush()
    return plant


async def get_plant(session: AsyncSession, plant_id: int, user_id: int) -> Plant | None:
    result = await session.execute(
        select(Plant).where(Plant.id == plant_id, Plant.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def find_plant_by_name(
    session: AsyncSession, user_id: int, name: str, group_id: int | None
) -> Plant | None:
    """Ищет растение с таким же именем (без учёта регистра) в той же
    группе (group_id=None -> среди растений без группы) — используется
    для проверки на повтор перед добавлением. Раз дубли разрешены (по
    подтверждению), совпадений может быть несколько — берём первое,
    а не scalar_one_or_none(), который упал бы с ошибкой на 2+."""
    result = await session.execute(
        select(Plant).where(
            Plant.user_id == user_id,
            Plant.group_id == group_id,
            func.lower(Plant.name) == name.strip().lower(),
        )
    )
    return result.scalars().first()


async def find_plant_by_name_any_group(
    session: AsyncSession, user_id: int, name: str
) -> Plant | None:
    """Как find_plant_by_name, но без учёта группы — ищет совпадение
    по имени среди всех растений пользователя. Используется для ранней
    проверки на повтор сразу после ввода названия, ещё до выбора группы."""
    result = await session.execute(
        select(Plant).where(
            Plant.user_id == user_id,
            func.lower(Plant.name) == name.strip().lower(),
        )
    )
    return result.scalars().first()


async def delete_plant(session: AsyncSession, plant: Plant) -> None:
    await session.delete(plant)
    await session.flush()


_UNSET = object()


async def update_plant(
    session: AsyncSession,
    plant: Plant,
    name: str | None = None,
    comment: str | None = _UNSET,  # type: ignore[assignment]
    group_id: int | None = _UNSET,  # type: ignore[assignment]
) -> None:
    """Обновляет поля растения. comment/group_id используют сентинел _UNSET,
    чтобы отличить "не менять" от "сбросить на None" (например, убрать из группы)."""
    if name is not None:
        plant.name = name.strip()
    if comment is not _UNSET:
        plant.comment = comment
    if group_id is not _UNSET:
        plant.group_id = group_id
    await session.flush()


async def get_full_tree(session: AsyncSession, user_id: int) -> tuple[list[Group], list[Plant]]:
    """Возвращает (группы с растениями, растения без группы) для общего списка."""
    groups_result = await session.execute(
        select(Group)
        .where(Group.user_id == user_id)
        .options(selectinload(Group.plants))
        .order_by(Group.name)
    )
    groups = list(groups_result.scalars())

    ungrouped_result = await session.execute(
        select(Plant).where(Plant.user_id == user_id, Plant.group_id.is_(None)).order_by(Plant.name)
    )
    ungrouped = list(ungrouped_result.scalars())

    return groups, ungrouped


# ---------- ИИ-логи ----------

async def create_ai_log(
    session: AsyncSession,
    user_id: int,
    user_text: str,
    action: str | None = None,
    plant_name: str | None = None,
    group_name: str | None = None,
    comment: str | None = None,
    error: str | None = None,
) -> AiLog:
    log = AiLog(
        user_id=user_id,
        user_text=user_text,
        action=action,
        plant_name=plant_name,
        group_name=group_name,
        comment=comment,
        error=error,
    )
    session.add(log)
    await session.flush()
    return log


async def list_ai_logs_for_user(session: AsyncSession, user_id: int, limit: int = 50) -> list[AiLog]:
    result = await session.execute(
        select(AiLog).where(AiLog.user_id == user_id).order_by(AiLog.id.desc()).limit(limit)
    )
    return list(result.scalars())


async def list_ai_logs_all(session: AsyncSession, limit: int = 100) -> list[tuple[AiLog, User | None]]:
    """Последние обращения к ИИ по всем пользователям сразу — для общего
    обзора в админке (что чаще всего пишут, где агент промахивается).
    User подтягивается через outerjoin, а не relationship: у AiLog
    намеренно нет ForeignKey на users (см. docstring модели), чтобы лог
    переживал удаление пользователя."""
    result = await session.execute(
        select(AiLog, User)
        .outerjoin(User, User.id == AiLog.user_id)
        .order_by(AiLog.id.desc())
        .limit(limit)
    )
    return [(log, user) for log, user in result.all()]
