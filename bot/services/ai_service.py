"""
Лёгкий ИИ-агент для команд вида "добавь алоказию полли в алоказии, полила вчера".

Работает с любым OpenAI-совместимым API, в т.ч. NVIDIA NIM
(https://integrate.api.nvidia.com/v1). Особенности NIM, которые здесь учтены:

- не все модели на NIM поддерживают response_format={"type": "json_object"} —
  при ошибке автоматически повторяем запрос без него;
- некоторые модели всё равно оборачивают ответ в ```json ... ``` несмотря
  на просьбу в промпте — вырезаем это перед парсингом;
- у части моделей (например ризонинг-модели) в ответе может быть блок
  рассуждений перед самим JSON — вытаскиваем JSON-объект по фигурным скобкам.
"""

import asyncio
import json
import re

import aiohttp

from bot.config import config

SYSTEM_PROMPT = """Ты помощник бота для учёта комнатных растений.
Пользователь пишет свободным текстом, что он хочет сделать.
Определи намерение и извлеки данные. Отвечай ТОЛЬКО валидным JSON, без пояснений, \
без markdown-разметки, без блоков кода — просто голый JSON-объект.

Формат ответа:
{
  "action": "add" | "delete" | "unknown",
  "plant_name": "название растения (с заглавной буквы) или null",
  "group_name": "название группы или null — определи по ботаническому роду, если возможно \
(например 'Алоказия Полли' -> группа 'Алоказии', 'Хавортия' -> 'Суккуленты'); \
если по контексту непонятно — верни null",
  "comment": "комментарий, если пользователь его указал, иначе null"
}

Если не можешь понять намерение — action: "unknown".
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class AIServiceUnavailable(Exception):
    pass


class AIServiceTimeout(AIServiceUnavailable):
    """Отдельно от прочих ошибок — нет смысла повторять запрос при таймауте сети."""
    pass


def _extract_json(content: str) -> dict:
    """Убирает markdown code fences и достаёт JSON-объект из ответа модели,
    даже если вокруг него есть лишний текст (типично для reasoning-моделей на NIM)."""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        return json.loads(match.group(0))

    raise json.JSONDecodeError("no JSON object found", cleaned, 0)


async def _call_api(user_text: str, use_json_mode: bool) -> str:
    headers = {
        "Authorization": f"Bearer {config.ai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.ai_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
    }
    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{config.ai_api_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise AIServiceUnavailable(f"AI API error {resp.status}: {text}")
                data = json.loads(text)
    except asyncio.TimeoutError as e:
        raise AIServiceTimeout(
            f"AI API не ответил за 30 секунд ({config.ai_api_base_url}) — "
            "похоже, сервер не может достучаться до хоста (проверь сеть/файрвол)"
        ) from e
    except aiohttp.ClientError as e:
        raise AIServiceUnavailable(f"Ошибка соединения с AI API: {e}") from e

    return data["choices"][0]["message"]["content"]


async def parse_intent(user_text: str) -> dict:
    if not config.ai_enabled:
        raise AIServiceUnavailable("ИИ-агент отключён (AI_ENABLED=false)")

    try:
        content = await _call_api(user_text, use_json_mode=True)
    except AIServiceTimeout:
        # сеть не работает — повторный запрос ничего не изменит, сразу пробрасываем
        raise
    except AIServiceUnavailable:
        # модель на NIM может не поддерживать response_format — пробуем без него
        content = await _call_api(user_text, use_json_mode=False)

    try:
        return _extract_json(content)
    except json.JSONDecodeError as e:
        raise AIServiceUnavailable(f"ИИ вернул не-JSON ответ: {content}") from e
