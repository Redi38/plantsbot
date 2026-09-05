"""
Лёгкий ИИ-агент для команд вида "добавь алоказию полли в алоказии, полила вчера".

В промпт передаётся полный список названий растений и групп пользователя —
это заметно повышает точность распознавания (модель сама сопоставляет
опечатки, сокращения, падежи и русское/латинское написание с точным
названием в базе), но при очень большом числе растений (сотни) ощутимо
увеличивает размер запроса и, соответственно, задержку/стоимость. Если это
станет проблемой — можно обрезать существующий_plants до, скажем, последних
N добавленных или до совпадающих по первым буквам с текстом пользователя.
"""

import asyncio
import json
import re
import time
from collections import OrderedDict

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
{plants_block}
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
Если пользователь хочет удалить РАСТЕНИЕ, но не называет его точно, а просит \
удалить "одно из" группы/рода (например "удали одну из алоказий", "хочу \
удалить какой-нибудь кактус, покажи список на удаление") — это тоже \
action="delete": верни group_name (название группы или рода, как в списке \
групп/по смыслу подходящее растениям), а plant_name оставь null. Бот сам \
покажет список подходящих растений на выбор.

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

_PLANTS_BLOCK_TEMPLATE = """
У пользователя уже есть такие растения: {plants_list}.
Если из текста понятно, что речь об одном из них — ДАЖЕ ЕСЛИ пользователь \
написал только часть названия, с опечаткой, оборвал слово на середине, \
перепутал падеж/окончание, использовал другую раскладку/транслитерацию \
или написал название на другом языке (например по-русски вместо латинского \
ботанического названия, как "алоказия карнаж" вместо "Alocasia Carnage") — \
верни plant_name ТОЧНО ТАК, КАК ОНО НАПИСАНО В ЭТОМ СПИСКЕ, посимвольно, \
включая регистр и язык. Это особенно важно для action="delete": лучше \
уверенно сопоставить с существующим растением из списка, чем вернуть текст \
пользователя как есть — сравнение потом идёт по точному совпадению строки.
Если под описание пользователя одинаково хорошо подходят НЕСКОЛЬКО разных \
растений из списка и непонятно, какое именно он имел в виду — верни \
plant_name так, как написал пользователь, не выбирая наугад: бот сам \
переспросит, какое из них он имел в виду.
Если растения с таким названием в списке точно нет (например пользователь \
явно добавляет новое, ранее не существовавшее) — верни название так, как он \
его написал, с заглавной буквы, как обычно.
"""

_NO_PLANTS_BLOCK = """
У пользователя пока нет ни одного растения в списке.
"""

_MAX_PLANTS_IN_PROMPT = 80


def _select_relevant_plants(user_text: str, existing_plants: list[str], limit: int) -> list[str]:
    if len(existing_plants) <= limit:
        return existing_plants

    text_lower = user_text.lower()
    text_words = [w for w in re.findall(r"[a-zа-яё]{3,}", text_lower)]

    relevant, rest = [], []
    for name in existing_plants:
        name_lower = name.lower()
        is_relevant = name_lower in text_lower or any(word in name_lower for word in text_words)
        (relevant if is_relevant else rest).append(name)

    result = relevant[:limit]
    if len(result) < limit:
        result += rest[: limit - len(result)]
    return result

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


def _build_system_prompt(existing_groups: list[str] | None, existing_plants: list[str] | None) -> str:
    if existing_groups:
        groups_block = _GROUPS_BLOCK_TEMPLATE.format(groups_list=", ".join(f"«{g}»" for g in existing_groups))
    else:
        groups_block = _NO_GROUPS_BLOCK
    if existing_plants:
        plants_block = _PLANTS_BLOCK_TEMPLATE.format(plants_list=", ".join(f"«{p}»" for p in existing_plants))
    else:
        plants_block = _NO_PLANTS_BLOCK
    return SYSTEM_PROMPT_TEMPLATE.format(groups_block=groups_block, plants_block=plants_block)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class AIServiceUnavailable(Exception):
    pass


class AIServiceTimeout(AIServiceUnavailable):
    """Отдельно от прочих ошибок — нет смысла повторять запрос при таймауте сети."""
    pass


class AIServiceRateLimited(AIServiceUnavailable):
    """429 от провайдера. _call_api уже пытается подождать и повторить сама
    (см. ниже) — наружу это исключение долетает только если провайдер
    отказал повторно и ждать ещё раз уже нет смысла в рамках одного
    пользовательского запроса."""
    pass


_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)


def _parse_retry_after(error_text: str) -> float:
    """Провайдер (например, Groq) сам подсказывает точное время ожидания в
    тексте ошибки ("Please try again in 16.1475s") — используем его вместо
    произвольной паузы. Если формат не совпал — разумный дефолт."""
    match = _RETRY_AFTER_RE.search(error_text)
    if match:
        return float(match.group(1))
    return 5.0


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


_CACHE_TTL_SECONDS = 120
_CACHE_MAX_ENTRIES = 500
_intent_cache: "OrderedDict[tuple, tuple[dict, float]]" = OrderedDict()


def _cache_key(
    user_id: int, user_text: str, existing_groups: list[str] | None, existing_plants: list[str] | None
) -> tuple:
    return (
        user_id,
        user_text.strip().lower(),
        tuple(existing_groups or ()),
        tuple(existing_plants or ()),
    )


def _cache_get(key: tuple) -> dict | None:
    entry = _intent_cache.get(key)
    if entry is None:
        return None
    intent, expires_at = entry
    if time.monotonic() > expires_at:
        _intent_cache.pop(key, None)
        return None
    _intent_cache.move_to_end(key)
    return intent


def _cache_set(key: tuple, intent: dict) -> None:
    _intent_cache[key] = (intent, time.monotonic() + _CACHE_TTL_SECONDS)
    _intent_cache.move_to_end(key)
    while len(_intent_cache) > _CACHE_MAX_ENTRIES:
        _intent_cache.popitem(last=False)


async def _call_api(
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
                        return await _call_api(
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


async def parse_intent(
    user_text: str,
    existing_groups: list[str] | None = None,
    existing_plants: list[str] | None = None,
    user_id: int | None = None,
) -> dict:
    if not config.ai_enabled:
        raise AIServiceUnavailable("ИИ-агент отключён (AI_ENABLED=false)")

    if existing_plants and len(existing_plants) > _MAX_PLANTS_IN_PROMPT:
        existing_plants = _select_relevant_plants(user_text, existing_plants, _MAX_PLANTS_IN_PROMPT)

    cache_key = None
    if user_id is not None:
        cache_key = _cache_key(user_id, user_text, existing_groups, existing_plants)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    system_prompt = _build_system_prompt(existing_groups, existing_plants)

    try:
        content = await _call_api(user_text, use_json_mode=True, system_prompt=system_prompt)
    except AIServiceTimeout:
        raise
    except AIServiceUnavailable:
        content = await _call_api(user_text, use_json_mode=False, system_prompt=system_prompt)

    try:
        intent = _extract_json(content)
    except json.JSONDecodeError as e:
        raise AIServiceUnavailable(f"ИИ вернул не-JSON ответ: {content}") from e

    if cache_key is not None:
        _cache_set(cache_key, intent)

    return intent
