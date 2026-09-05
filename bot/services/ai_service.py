"""
Лёгкий ИИ-агент для команд вида "добавь алоказию полли в алоказии, полила вчера".
"""

import asyncio
import json
import re

import aiohttp

from bot.config import config

SYSTEM_PROMPT_TEMPLATE = """Ты помощник бота для учёта комнатных растений.
Пользователь пишет свободным текстом, что он хочет сделать.
Определи намерение и извлеки данные. Отвечай ТОЛЬКО валидным JSON, без пояснений, \
без markdown-разметки, без блоков кода — просто голый JSON-объект.

Формат ответа:
{{
  "action": "add" | "delete" | "delete_group" | "create_group" | "unknown",
  "plant_name": "название растения (с заглавной буквы) или null",
  "group_name": "название группы или null",
  "comment": "комментарий, если пользователь его указал, иначе null"
}}

action="delete_group" — когда пользователь просит удалить ЦЕЛИКОМ группу \
растений (например "удали группу Суккуленты", "снеси группу Кактусы вместе со \
всеми растениями"). group_name — название удаляемой группы, plant_name и \
comment — null. Что делать с растениями внутри (перенести или удалить вместе \
с группой) уточнит сам бот отдельными кнопками — даже если пользователь уже \
написал "вместе с растениями" или "растения перенеси", всё равно верни просто \
action="delete_group", не пытайся закодировать этот выбор в ответе.
Если пользователь просит удалить конкретное РАСТЕНИЕ (даже если заодно \
упомянута группа, например "удали алоэ из группы Суккуленты") — это \
action="delete", а не "delete_group".

action="create_group" — когда пользователь явно просит создать/завести новую \
группу саму по себе, без привязки к конкретному растению (например "создай \
группу Суккуленты", "заведи группу для орхидей", "сделай группу Кактусы"). \
В этом случае group_name — название группы, которую нужно создать, \
plant_name и comment — null. Если растение упомянуто вместе с группой \
(например "добавь алоэ в группу Суккуленты") — это action="add", а не \
"create_group".
{groups_block}
Если не можешь понять намерение — action: "unknown".
"""

_GROUPS_BLOCK_TEMPLATE = """
У пользователя уже есть такие группы растений: {groups_list}.
Если пользователь ЯВНО указал в тексте, в какую группу добавить растение \
(например "добавь X в группу Y") — верни group_name ТОЧНО ТАК, как написал \
пользователь, даже если такой группы ещё нет в списке выше (её создадут отдельно). \
Единственное исключение — если написанное пользователем явно совпадает по смыслу \
с одной из существующих групп (опечатка, другой падеж/число) — тогда верни её \
название ТОЧНО ТАК, КАК ОНО НАПИСАНО В ЭТОМ СПИСКЕ, посимвольно.
Если пользователь группу явно не назвал, но добавляемое растение по смыслу \
(виду/роду) подходит к одной из существующих групп — верни её название из списка. \
Не изобретай новую группу, если подходящая уже есть.
Если группа явно не названа и ни одна существующая не подходит, а стоит завести \
новую — предложи короткое название по ботаническому роду (например 'Алоказия \
Полли' -> 'Алоказии'). Если группу определить не получается — верни null.
"""

_NO_GROUPS_BLOCK = """
У пользователя пока нет ни одной группы растений. Если пользователь ЯВНО указал \
название группы в тексте — верни его точно так, как он написал (группа будет \
создана вместе с добавлением растения). Если группу явно не назвал, но понятно, \
что растение относится к определённому ботаническому роду — предложи короткое \
название группы по этому роду (например 'Алоказия Полли' -> 'Алоказии'). Если \
непонятно — верни null.
"""


def _build_system_prompt(existing_groups: list[str] | None) -> str:
    if existing_groups:
        groups_block = _GROUPS_BLOCK_TEMPLATE.format(groups_list=", ".join(f"«{g}»" for g in existing_groups))
    else:
        groups_block = _NO_GROUPS_BLOCK
    return SYSTEM_PROMPT_TEMPLATE.format(groups_block=groups_block)

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


async def _call_api(user_text: str, use_json_mode: bool, system_prompt: str) -> str:
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


async def parse_intent(user_text: str, existing_groups: list[str] | None = None) -> dict:
    if not config.ai_enabled:
        raise AIServiceUnavailable("ИИ-агент отключён (AI_ENABLED=false)")

    system_prompt = _build_system_prompt(existing_groups)

    try:
        content = await _call_api(user_text, use_json_mode=True, system_prompt=system_prompt)
    except AIServiceTimeout:
        raise
    except AIServiceUnavailable:
        content = await _call_api(user_text, use_json_mode=False, system_prompt=system_prompt)

    try:
        return _extract_json(content)
    except json.JSONDecodeError as e:
        raise AIServiceUnavailable(f"ИИ вернул не-JSON ответ: {content}") from e
