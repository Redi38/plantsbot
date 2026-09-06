"""
Лёгкий ИИ-агент для команд вида "добавь алоказию полли в алоказии, полила вчера".

Публичный интерфейс модуля не изменился при разбиении на подмодули:
- parse_intent — основная функция
- AIServiceUnavailable / AIServiceTimeout / AIServiceRateLimited — исключения

Внутреннее устройство разложено по файлам:
- prompt.py — шаблоны и построение системного промпта
- client.py — HTTP-вызов AI API и разбор JSON-ответа
- cache.py — кэш распознанных намерений
- exceptions.py — иерархия исключений
"""

import json

from bot.config import config

from .cache import cache_get, cache_key, cache_set
from .client import call_api, close_session, extract_json
from .exceptions import AIServiceRateLimited, AIServiceTimeout, AIServiceUnavailable
from .prompt import _MAX_PLANTS_IN_PROMPT, build_system_prompt, select_relevant_plants

__all__ = [
    "AIServiceRateLimited",
    "AIServiceTimeout",
    "AIServiceUnavailable",
    "close_session",
    "parse_intent",
]


async def parse_intent(
    user_text: str,
    existing_groups: list[str] | None = None,
    existing_plants: list[str] | None = None,
    user_id: int | None = None,
) -> dict:
    if not config.ai_enabled:
        raise AIServiceUnavailable("ИИ-агент отключён (AI_ENABLED=false)")

    if existing_plants and len(existing_plants) > _MAX_PLANTS_IN_PROMPT:
        existing_plants = select_relevant_plants(user_text, existing_plants, _MAX_PLANTS_IN_PROMPT)

    key = None
    if user_id is not None:
        key = cache_key(user_id, user_text, existing_groups, existing_plants)
        cached = cache_get(key)
        if cached is not None:
            return cached

    system_prompt = build_system_prompt(existing_groups, existing_plants)

    used_json_mode = True
    try:
        content = await call_api(user_text, use_json_mode=True, system_prompt=system_prompt)
    except AIServiceTimeout:
        raise
    except AIServiceUnavailable:
        used_json_mode = False
        content = await call_api(user_text, use_json_mode=False, system_prompt=system_prompt)

    try:
        intent = extract_json(content)
    except json.JSONDecodeError as e:
        if not used_json_mode:
            raise AIServiceUnavailable(f"ИИ вернул не-JSON ответ: {content}") from e
        content = await call_api(user_text, use_json_mode=False, system_prompt=system_prompt)
        try:
            intent = extract_json(content)
        except json.JSONDecodeError as e:
            raise AIServiceUnavailable(f"ИИ вернул не-JSON ответ: {content}") from e

    if key is not None:
        cache_set(key, intent)

    return intent
