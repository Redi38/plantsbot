"""Логика аптечки для растений (средства от вредителей и болезней, удобрения):
добавление/удаление препаратов, разбор срока
годности и тексты для сообщений. Всё, что связано с датами, принимает today
параметром (по умолчанию — сегодня по UTC), чтобы тесты не зависели от
реальных часов."""

import calendar
import re
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


_PAGE_LIMIT = 3800  # запас относительно лимита Telegram в 4096 символов


def _mark(medicine: Medicine, today: date) -> str:
    if is_expired(medicine, today):
        return " ⛔"
    if is_expiring_soon(medicine, today):
        return " ⚠️"
    return ""


def _render_entry(medicine: Medicine, today: date, with_kind: bool = False) -> str:
    """Один препарат — небольшой блок обычного текста. Настоящих таблиц в
    Telegram нет, а моноширинная (<pre>) на телефоне ломается и уезжает за
    край экрана, поэтому поля идут строками друг под другом и сами
    переносятся по ширине экрана. Пустые поля не показываем."""
    title = f"<b>{escape(medicine.name)}</b>"
    if with_kind:
        title += f" · {escape(medicine.kind.strip())}"
    lines = [f"{title}{_mark(medicine, today)}"]
    if medicine.active_substance:
        lines.append(f"Вещество: {escape(medicine.active_substance)}")
    if medicine.expires_at:
        lines.append(f"Срок: {format_expiry(medicine.expires_at)}")
    if medicine.comment:
        lines.append(f"Комментарий: {escape(medicine.comment)}")
    return "\n".join(lines)


def _render_entries(medicines: list[Medicine], today: date, with_kind: bool = False) -> str:
    return "\n\n".join(_render_entry(m, today, with_kind) for m in medicines)


def _legend(medicines: list[Medicine], today: date) -> str:
    parts = []
    if any(is_expired(m, today) for m in medicines):
        parts.append("⛔ срок вышел")
    if any(is_expiring_soon(m, today) for m in medicines):
        parts.append("⚠️ скоро закончится срок")
    return "\n" + "   ".join(parts) if parts else ""


def _paginate(prefix: str, medicines: list[Medicine], today: date, with_kind: bool = False) -> list[str]:
    """Режет список препаратов на страницы так, чтобы каждая влезала в лимит
    Telegram; препарат целиком остаётся на одной странице."""

    def page(chunk: list[Medicine]) -> str:
        return f"{prefix}\n\n{_render_entries(chunk, today, with_kind)}{_legend(chunk, today)}"

    pages: list[str] = []
    chunk: list[Medicine] = []
    for medicine in medicines:
        candidate = [*chunk, medicine]
        if chunk and len(page(candidate)) > _PAGE_LIMIT:
            pages.append(page(chunk))
            chunk = [medicine]
        else:
            chunk = candidate
    pages.append(page(chunk))
    return pages


def render_group_pages(group: KindGroup, today: date) -> list[str]:
    """Страницы с препаратами одного типа."""
    return _paginate(f"<b>{escape(group.name)} ({len(group.medicines)})</b>", group.medicines, today)


def render_all_pages(medicines: list[Medicine], today: date) -> list[str]:
    """Общий список аптечки: все препараты вместе, у каждого указан тип
    (порядок — по типам, как в меню). Если не влезает в лимит Telegram,
    делится на страницы."""
    if not medicines:
        return [render_overview(medicines, today)]
    ordered = [m for group in group_by_kind(medicines) for m in group.medicines]
    return _paginate(f"🧪 <b>Вся аптечка ({len(ordered)})</b>", ordered, today, with_kind=True)


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
