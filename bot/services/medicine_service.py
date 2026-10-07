"""Логика аптечки для растений (средства от вредителей и болезней, удобрения):
добавление/удаление препаратов, разбор срока
годности и тексты для сообщений. Всё, что связано с датами, принимает today
параметром (по умолчанию — сегодня по UTC), чтобы тесты не зависели от
реальных часов."""

import calendar
import re
import textwrap
import zlib
from datetime import date, datetime, timezone
from html import escape
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import crud
from bot.db.models import Medicine

MAX_NAME_LENGTH = 150
MAX_KIND_LENGTH = 50
MAX_SUBSTANCE_LENGTH = 150
MAX_COMMENT_LENGTH = 500
# Верхняя граница на пользователя: в экране удаления каждый препарат — отдельная
# кнопка, а у Telegram есть лимит на число кнопок в одной клавиатуре.
MAX_MEDICINES = 90

# За сколько дней до конца срока годности приходит напоминание.
NOTIFY_DAYS_BEFORE = 30
# С какого часа (UTC) отправляем напоминания: 06:00 UTC = 09:00 по Минску
# (см. DISPLAY_UTC_OFFSET в watering_service), чтобы не будить людей ночью.
NOTIFY_HOUR_UTC = 6

# Быстрый выбор типа кнопками; свой тип можно написать в чат.
KIND_PRESETS = (
    "Инсектицид",
    "Фунгицид",
    "Акарицид",
    "Биопрепарат",
    "Мыло / масло",
    "Удобрение",
    "Стимулятор роста",
    "Системные гранулы",
)

_MONTH_YEAR_RE = re.compile(r"(\d{1,2})[./\-\s](\d{4})")
_FULL_DATE_RE = re.compile(r"(\d{1,2})[./\-\s](\d{1,2})[./\-\s](\d{4})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_ISO_MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")

MIN_YEAR = 2000
MAX_YEAR = 2100


class TooManyMedicines(Exception):
    """В аптечке уже MAX_MEDICINES препаратов."""


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_expiry(text: str) -> date | None:
    """Разбирает срок годности из пользовательского ввода.

    Понимает «05.2027» / «5/2027» / «2027-05» (месяц и год, как пишут на
    упаковке — тогда срок считается до конца этого месяца) и полные даты
    «31.05.2027» / «31/5/2027» / «2027-05-31». Возвращает None, если это не
    дата (несуществующий день или месяц, год вне разумных границ) —
    вызывающий код в этом случае просит ввести заново."""
    value = text.strip()
    try:
        if m := _FULL_DATE_RE.fullmatch(value):
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            result = date(year, month, day)
        elif m := _ISO_DATE_RE.fullmatch(value):
            result = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif m := _MONTH_YEAR_RE.fullmatch(value):
            result = _end_of_month(int(m.group(2)), int(m.group(1)))
        elif m := _ISO_MONTH_RE.fullmatch(value):
            result = _end_of_month(int(m.group(1)), int(m.group(2)))
        else:
            return None
    except ValueError:
        return None
    if not MIN_YEAR <= result.year <= MAX_YEAR:
        return None
    return result


def format_expiry(expires_at: date) -> str:
    """«05.2027», если срок — последний день месяца (так пишут на упаковке),
    иначе полная дата «15.05.2027»."""
    if expires_at == _end_of_month(expires_at.year, expires_at.month):
        return expires_at.strftime("%m.%Y")
    return expires_at.strftime("%d.%m.%Y")


def days_left(expires_at: date, today: date) -> int:
    return (expires_at - today).days


def is_expired(medicine: Medicine, today: date) -> bool:
    return medicine.expires_at is not None and medicine.expires_at < today


def is_expiring_soon(medicine: Medicine, today: date) -> bool:
    """Срок ещё не вышел, но уже попадает в окно напоминания."""
    return medicine.expires_at is not None and 0 <= days_left(medicine.expires_at, today) <= NOTIFY_DAYS_BEFORE


def describe_remaining(expires_at: date, today: date) -> str:
    left = days_left(expires_at, today)
    if left < 0:
        return f"истёк {-left} дн. назад"
    if left == 0:
        return "истекает сегодня"
    return f"осталось {left} дн."


async def add_medicine(
    session: AsyncSession,
    user_id: int,
    name: str,
    kind: str,
    active_substance: str | None = None,
    expires_at: date | None = None,
    comment: str | None = None,
) -> Medicine:
    """Создаёт препарат. Обязательны только название и тип; пустые строки в
    необязательных полях превращаются в None."""
    name, kind = name.strip(), kind.strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name must be 1..{MAX_NAME_LENGTH} chars")
    if not kind or len(kind) > MAX_KIND_LENGTH:
        raise ValueError(f"kind must be 1..{MAX_KIND_LENGTH} chars")
    active_substance = (active_substance or "").strip() or None
    comment = (comment or "").strip() or None
    if active_substance and len(active_substance) > MAX_SUBSTANCE_LENGTH:
        raise ValueError(f"active_substance must be <= {MAX_SUBSTANCE_LENGTH} chars")
    if comment and len(comment) > MAX_COMMENT_LENGTH:
        raise ValueError(f"comment must be <= {MAX_COMMENT_LENGTH} chars")
    if await crud.count_medicines(session, user_id) >= MAX_MEDICINES:
        raise TooManyMedicines
    medicine = await crud.create_medicine(session, user_id, name, kind, active_substance, expires_at, comment)
    await session.commit()
    return medicine


async def remove(session: AsyncSession, medicine: Medicine) -> None:
    await crud.delete_medicine(session, medicine)
    await session.commit()


# ---------- Тексты ----------


def _icon(medicine: Medicine, today: date) -> str:
    if is_expired(medicine, today):
        return "⛔"
    if is_expiring_soon(medicine, today):
        return "⚠️"
    return "🧪"


def button_label(medicine: Medicine, today: date) -> str:
    """Подпись кнопки препарата в меню (лимит Telegram — 64 символа)."""
    name = medicine.name if len(medicine.name) <= 35 else medicine.name[:34] + "…"
    label = f"{_icon(medicine, today)} {name}"
    if medicine.expires_at:
        label += f" · {format_expiry(medicine.expires_at)}"
    return label


class KindGroup(NamedTuple):
    """Препараты одного типа (группа в меню аптечки, как группа растений)."""

    token: str
    name: str
    medicines: list[Medicine]


def kind_token(kind: str) -> str:
    """Короткий стабильный id типа для callback_data (лимит Telegram — 64
    байта, а тип может быть длинным русским текстом). Регистр и пробелы по
    краям не важны: «Фунгицид» и «фунгицид » — один тип."""
    return format(zlib.crc32(kind.strip().casefold().encode()) & 0xFFFFFFFF, "08x")


def group_by_kind(medicines: list[Medicine]) -> list[KindGroup]:
    """Разбивает препараты по типам. Сначала типы из KIND_PRESETS в их порядке,
    потом свои — по алфавиту. Внутри группы порядок как на входе (из
    crud.list_medicines: сначала те, у кого срок раньше)."""
    buckets: dict[str, KindGroup] = {}
    for medicine in medicines:
        token = kind_token(medicine.kind)
        if token not in buckets:
            buckets[token] = KindGroup(token, medicine.kind.strip(), [])
        buckets[token].medicines.append(medicine)

    preset_order = {kind_token(kind): index for index, kind in enumerate(KIND_PRESETS)}
    return sorted(
        buckets.values(),
        key=lambda g: (preset_order.get(g.token, len(KIND_PRESETS)), g.name.casefold()),
    )


def find_group(groups: list[KindGroup], token: str) -> KindGroup | None:
    return next((g for g in groups if g.token == token), None)


# Колонки таблицы: (заголовок, ширина в символах). Длинный текст переносится
# внутри ячейки на следующую строку, поэтому ничего не обрезается.
_TABLE_COLUMNS = (("Название", 14), ("Вещество", 12), ("Срок", 10), ("Комментарий", 16))
_TABLE_PAGE_LIMIT = 3800  # запас относительно лимита Telegram в 4096 символов


def _wrap_cell(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width, break_long_words=True, break_on_hyphens=False) or [""]


def _row_cells(medicine: Medicine) -> list[str]:
    expiry = format_expiry(medicine.expires_at) if medicine.expires_at else "—"
    return [medicine.name, medicine.active_substance or "—", expiry, medicine.comment or "—"]


def _render_table(medicines: list[Medicine], today: date) -> str:
    """Моноширинная таблица (<pre>) со всеми полями препарата. Значок срока
    (⛔/⚠️) стоит за правым краем таблицы — эмодзи шире обычного символа и
    сбивали бы выравнивание колонок, будь они внутри ячейки."""
    widths = [w for _, w in _TABLE_COLUMNS]
    header = " │ ".join(title.ljust(w) for title, w in _TABLE_COLUMNS)
    rule = "─┼─".join("─" * w for w in widths)

    rows: list[tuple[list[list[str]], str]] = []
    for medicine in medicines:
        cells = [_wrap_cell(value, w) for value, w in zip(_row_cells(medicine), widths, strict=True)]
        mark = ""
        if is_expired(medicine, today):
            mark = " ⛔"
        elif is_expiring_soon(medicine, today):
            mark = " ⚠️"
        rows.append((cells, mark))

    spaced = any(max(len(c) for c in cells) > 1 for cells, _ in rows)
    lines = [header, rule]
    for index, (cells, mark) in enumerate(rows):
        height = max(len(c) for c in cells)
        for i in range(height):
            parts = [(c[i] if i < len(c) else "").ljust(w) for c, w in zip(cells, widths, strict=True)]
            line = " │ ".join(parts)
            lines.append(line + mark if i == 0 and mark else line.rstrip())
        if spaced and index < len(rows) - 1:
            lines.append(rule)
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def _legend(medicines: list[Medicine], today: date) -> str:
    parts = []
    if any(is_expired(m, today) for m in medicines):
        parts.append("⛔ срок вышел")
    if any(is_expiring_soon(m, today) for m in medicines):
        parts.append("⚠️ скоро закончится срок")
    return "\n" + "   ".join(parts) if parts else ""


def render_group_pages(group: KindGroup, today: date, header: str | None = None) -> list[str]:
    """Страницы с таблицей одного типа. Строки не разрываются между
    страницами, а каждая страница — отдельная законченная <pre>-таблица
    (резать готовый HTML по строкам нельзя: теги разъедутся). header —
    необязательная шапка над названием типа (в режиме «Показать все»)."""
    title = f"<b>{escape(group.name)} ({len(group.medicines)})</b>"
    prefix = f"{header}\n\n{title}" if header else title

    pages: list[str] = []
    chunk: list[Medicine] = []
    for medicine in group.medicines:
        candidate = [*chunk, medicine]
        text = f"{prefix}\n{_render_table(candidate, today)}{_legend(candidate, today)}"
        if chunk and len(text) > _TABLE_PAGE_LIMIT:
            pages.append(f"{prefix}\n{_render_table(chunk, today)}{_legend(chunk, today)}")
            chunk = [medicine]
        else:
            chunk = candidate
    pages.append(f"{prefix}\n{_render_table(chunk, today)}{_legend(chunk, today)}")
    return pages


def render_all_pages(medicines: list[Medicine], today: date) -> list[str]:
    """«Показать все»: каждый тип — своя таблица на своей странице."""
    if not medicines:
        return [render_overview(medicines, today)]
    header = f"🧪 <b>Вся аптечка ({len(medicines)})</b>"
    pages: list[str] = []
    for group in group_by_kind(medicines):
        pages.extend(render_group_pages(group, today, header))
    return pages


def render_pick_text(group_name: str | None) -> str:
    where = f" из типа «{escape(group_name)}»" if group_name else ""
    return f"🗑 Какой препарат{where} удалить?"


def render_overview(medicines: list[Medicine], today: date) -> str:
    if not medicines:
        return (
            "🧪 <b>Аптечка</b>\n\n"
            "Пока пусто. Здесь можно хранить средства для обработки растений — от вредителей, болезней, "
            "подкормки. Добавь препарат: название, тип и срок годности, "
            f"а за {NOTIFY_DAYS_BEFORE} дней до конца срока я напомню."
        )
    expired = sum(is_expired(m, today) for m in medicines)
    soon = sum(is_expiring_soon(m, today) for m in medicines)
    lines = [f"🧪 <b>Аптечка</b>\n\nПрепаратов: {len(medicines)}"]
    if expired:
        lines.append(f"⛔ Срок вышел: {expired}")
    if soon:
        lines.append(f"⚠️ Скоро закончится срок: {soon}")
    lines.append("\nВыбери тип:")
    return "\n".join(lines)


def render_card(medicine: Medicine, today: date) -> str:
    lines = [f"{_icon(medicine, today)} <b>{escape(medicine.name)}</b>\n", f"Тип: {escape(medicine.kind)}"]
    if medicine.active_substance:
        lines.append(f"Действующее вещество: {escape(medicine.active_substance)}")
    if medicine.expires_at:
        lines.append(
            f"Срок годности: до {format_expiry(medicine.expires_at)} ({describe_remaining(medicine.expires_at, today)})"
        )
    else:
        lines.append("Срок годности: не указан")
    if medicine.comment:
        lines.append(f"Комментарий: {escape(medicine.comment)}")
    return "\n".join(lines)


def render_reminder(medicine: Medicine, today: date) -> str:
    assert medicine.expires_at is not None
    title = f"«{escape(medicine.name)}» ({escape(medicine.kind.lower())})"
    until = format_expiry(medicine.expires_at)
    left = days_left(medicine.expires_at, today)
    if left < 0:
        return f"⛔ У препарата {title} срок годности истёк ({until}). Проверь аптечку."
    if left == 0:
        return f"⚠️ У препарата {title} срок годности заканчивается сегодня ({until})."
    return f"⚠️ У препарата {title} скоро заканчивается срок годности: до {until}, осталось {left} дн."
