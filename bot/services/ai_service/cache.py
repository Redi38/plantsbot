"""Кэш распознанных намерений — чтобы не дёргать AI API повторно на
идентичный запрос от того же пользователя в рамках TTL."""

import time
from collections import OrderedDict

_CACHE_TTL_SECONDS = 120
_CACHE_MAX_ENTRIES = 500
_intent_cache: "OrderedDict[tuple, tuple[dict, float]]" = OrderedDict()


def cache_key(
    user_id: int, user_text: str, existing_groups: list[str] | None, existing_plants: list[str] | None
) -> tuple:
    return (
        user_id,
        user_text.strip().lower(),
        tuple(existing_groups or ()),
        tuple(existing_plants or ()),
    )


def cache_get(key: tuple) -> dict | None:
    entry = _intent_cache.get(key)
    if entry is None:
        return None
    intent, expires_at = entry
    if time.monotonic() > expires_at:
        _intent_cache.pop(key, None)
        return None
    _intent_cache.move_to_end(key)
    return intent


def cache_set(key: tuple, intent: dict) -> None:
    _intent_cache[key] = (intent, time.monotonic() + _CACHE_TTL_SECONDS)
    _intent_cache.move_to_end(key)
    while len(_intent_cache) > _CACHE_MAX_ENTRIES:
        _intent_cache.popitem(last=False)
