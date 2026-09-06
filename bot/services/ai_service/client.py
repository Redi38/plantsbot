"""HTTP-вызов AI API и разбор ответа модели."""

import asyncio
import json
import re

import aiohttp

from bot.config import config

from .exceptions import AIServiceRateLimited, AIServiceTimeout, AIServiceUnavailable

_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_retry_after(error_text: str) -> float:
    """Провайдер (например, Groq) сам подсказывает точное время ожидания в
    тексте ошибки ("Please try again in 16.1475s") — используем его вместо
    произвольной паузы. Если формат не совпал — разумный дефолт."""
    match = _RETRY_AFTER_RE.search(error_text)
    if match:
        return float(match.group(1))
    return 5.0


def extract_json(content: str) -> dict:
    """Убирает markdown code fences и достаёт JSON-объект из ответа модели,
    даже если вокруг него есть лишний текст (типично для reasoning-моделей на NIM).
    Также чинит самую частую поломку валидного на вид JSON от LLM — висячую
    запятую перед закрывающей скобкой (`"a": 1,}` / `[1, 2,]`), которую сам
    json.loads не переваривает."""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    def _try_parse(text: str) -> dict | None:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            fixed = re.sub(r",(\s*[}\]])", r"\1", text)
            if fixed != text:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    return None
            return None

    result = _try_parse(cleaned)
    if result is not None:
        return result

    match = _JSON_OBJECT_RE.search(cleaned)
    if match:
        result = _try_parse(match.group(0))
        if result is not None:
            return result

    raise json.JSONDecodeError("no JSON object found", cleaned, 0)


async def call_api(
    user_text: str, use_json_mode: bool, system_prompt: str, *, _retry_on_rate_limit: bool = True
) -> str:
    headers = {
        "Authorization": f"Bearer {config.ai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.ai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
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
                if resp.status == 429:
                    if _retry_on_rate_limit:
                        wait_seconds = min(_parse_retry_after(text), 25.0) + 0.5
                        await asyncio.sleep(wait_seconds)
                        return await call_api(
                            user_text, use_json_mode, system_prompt, _retry_on_rate_limit=False
                        )
                    raise AIServiceRateLimited(f"AI API rate limit не прошёл даже после ожидания: {text}")
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
