"""Разбор полей intent для сценариев зон полива: числа дней и времени
напоминания, как их может вернуть модель, плюс общие константы/тексты
ошибок, используемые в apply.py и handlers.py."""

from datetime import time

from bot.services import watering_service
from bot.services.watering_service import MAX_INTERVAL_DAYS, MIN_INTERVAL_DAYS

NOT_FOUND = "⚠️ Зона не найдена, возможно уже удалена."
INTERVAL_ERROR = f"⚠️ Число дней должно быть от {MIN_INTERVAL_DAYS} до {MAX_INTERVAL_DAYS}."

# Статусы разбора notify_time из intent.
TIME_MISSING = "missing"  # не названо (null)
TIME_CLEAR = "clear"      # "" — убрать фиксированное время
TIME_INVALID = "invalid"  # что-то есть, но это не время
TIME_SET = "set"


def text(intent: dict, key: str) -> str:
    value = intent.get(key)
    return value.strip() if isinstance(value, str) else ""


def read_days(value: object) -> tuple[int | None, bool]:
    """(дни, корректно_ли). Не названо (None / "") -> (None, True): вызывающий
    решает, что делать без значения. Названо, но не число или вне
    1..365 -> (None, False)."""
    if value is None or value == "":
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        days = value
    elif isinstance(value, str):
        parsed = watering_service.parse_interval(value)
        if parsed is None:
            return None, False
        days = parsed
    else:
        return None, False
    if not MIN_INTERVAL_DAYS <= days <= MAX_INTERVAL_DAYS:
        return None, False
    return days, True


def read_notify_time(value: object) -> tuple[time | None, str]:
    """(время_в_UTC, статус). Время от модели — локальное (как на кнопках),
    в UTC его переводим здесь."""
    if value is None:
        return None, TIME_MISSING
    if not isinstance(value, str):
        return None, TIME_INVALID
    if not value.strip():
        return None, TIME_CLEAR
    parsed = watering_service.parse_notify_time(value)
    if parsed is None:
        return None, TIME_INVALID
    return watering_service.from_display_time(parsed), TIME_SET
