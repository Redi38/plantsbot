"""
Импорт списков растений.

Поддерживаются два формата:

1. CSV с колонками group,name,comment (заголовок обязателен):
    group,name,comment
    Алоказии,Алоказия Полли,пересадила в марте
    Алоказии,Алоказия Одора,
    Суккуленты,Хавортия,

2. Markdown-текст (удобно просить именно в таком виде у GPT):
    Алоказии:
    - Алоказия Полли: пересадила в марте
    - Алоказия Одора

    Суккуленты:
    - Хавортия

Оба парсера возвращают список ImportRow — "сырые" строки без сохранения в БД.
Отдельно build_preview() сверяет группы из импорта с уже существующими
(регистронезависимо), чтобы показать пользователю, что смэтчится,
а что создастся заново — и дать подтвердить/отменить перед записью.
"""

import csv
import io
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud


@dataclass
class ImportRow:
    group_name: str | None
    plant_name: str
    comment: str | None


@dataclass
class PreviewGroup:
    name: str
    is_new: bool
    matched_existing_name: str | None  # если смэтчилось с уже существующей группой под другим регистром/пробелами
    plants: list[ImportRow]


class ImportParseError(Exception):
    pass


def parse_csv(raw_text: str) -> list[ImportRow]:
    reader = csv.DictReader(io.StringIO(raw_text))
    if reader.fieldnames is None or "name" not in reader.fieldnames:
        raise ImportParseError(
            "Не нашла колонку 'name' в CSV. Ожидаю заголовок вида: group,name,comment"
        )

    rows: list[ImportRow] = []
    for raw in reader:
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        group = (raw.get("group") or "").strip() or None
        comment = (raw.get("comment") or "").strip() or None
        rows.append(ImportRow(group_name=group, plant_name=name, comment=comment))

    if not rows:
        raise ImportParseError("В CSV не найдено ни одной строки с растением.")
    return rows


def parse_markdown(raw_text: str) -> list[ImportRow]:
    """
    Заголовок вида "Название:" (без ведущего "-") → группа.
    Строка вида "- Название" или "- Название: комментарий" → растение.
    Строки без группы выше (или до первого заголовка) идут без группы.
    """
    rows: list[ImportRow] = []
    current_group: str | None = None

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("-") or line.startswith("*"):
            content = line.lstrip("-*").strip()
            if not content:
                continue
            if ":" in content:
                name, comment = content.split(":", 1)
                name = name.strip()
                comment = comment.strip() or None
            else:
                name, comment = content, None
            if name:
                rows.append(ImportRow(group_name=current_group, plant_name=name, comment=comment))
        elif line.endswith(":"):
            current_group = line[:-1].strip()
        else:
            # строка без "-" и без ":" в конце — считаем растением без группы,
            # если группа выше ещё не задана, иначе просто пропускаем как непонятную
            if current_group is None:
                rows.append(ImportRow(group_name=None, plant_name=line, comment=None))

    if not rows:
        raise ImportParseError(
            "Не удалось распознать ни одного растения. Проверь формат — "
            "группа как 'Название:', растение как '- Название'."
        )
    return rows


async def build_preview(session: AsyncSession, user_id: int, rows: list[ImportRow]) -> list[PreviewGroup]:
    """Группирует строки импорта по названию группы и сверяет с уже существующими."""
    grouped: dict[str, list[ImportRow]] = {}
    order: list[str] = []
    ungrouped: list[ImportRow] = []

    for row in rows:
        if row.group_name is None:
            ungrouped.append(row)
            continue
        key = row.group_name
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)

    preview: list[PreviewGroup] = []
    for name in order:
        existing = await crud.get_group_by_name(session, user_id, name)
        preview.append(
            PreviewGroup(
                name=name,
                is_new=existing is None,
                matched_existing_name=existing.name if existing else None,
                plants=grouped[name],
            )
        )

    if ungrouped:
        preview.append(PreviewGroup(name="Без группы", is_new=False, matched_existing_name=None, plants=ungrouped))

    return preview


def render_preview_text(preview: list[PreviewGroup]) -> str:
    lines = ["<b>Предпросмотр импорта:</b>\n"]
    total_plants = 0
    for pg in preview:
        total_plants += len(pg.plants)
        if pg.name == "Без группы":
            lines.append(f"📁 <b>Без группы</b> ({len(pg.plants)} шт.)")
        elif pg.is_new:
            lines.append(f"🆕 <b>{pg.name}</b> — новая группа ({len(pg.plants)} шт.)")
        else:
            lines.append(
                f"✅ <b>{pg.name}</b> → добавится в существующую «{pg.matched_existing_name}» ({len(pg.plants)} шт.)"
            )
        for row in pg.plants:
            lines.append(f"   • {row.plant_name}")
    lines.append(f"\nВсего растений: {total_plants}")
    lines.append("\nПодтвердить импорт?")
    return "\n".join(lines)


async def commit_import(session: AsyncSession, user_id: int, preview: list[PreviewGroup]) -> int:
    """Сохраняет предпросмотренный импорт в БД. Возвращает число добавленных растений."""
    from bot.db import crud as _crud  # локальный импорт во избежание циклов

    count = 0
    for pg in preview:
        group_id = None
        if pg.name != "Без группы":
            group, _ = await _crud.get_or_create_group(session, user_id, pg.name)
            group_id = group.id
        for row in pg.plants:
            await _crud.create_plant(
                session, user_id, row.plant_name, group_id=group_id, comment=row.comment
            )
            count += 1

    await session.commit()
    return count
