import json

import pytest

from bot.services.ai_service.client import _parse_retry_after, extract_json


# --- extract_json ----------------------------------------------------------


def test_extract_json_plain():
    assert extract_json('{"action": "add"}') == {"action": "add"}


def test_extract_json_strips_markdown_fences():
    content = '```json\n{"action": "add"}\n```'
    assert extract_json(content) == {"action": "add"}


def test_extract_json_strips_fence_without_language_tag():
    content = '```\n{"action": "add"}\n```'
    assert extract_json(content) == {"action": "add"}


def test_extract_json_fixes_trailing_comma():
    assert extract_json('{"action": "add", "plant_name": "Алоэ",}') == {"action": "add", "plant_name": "Алоэ"}


def test_extract_json_fixes_trailing_comma_in_array():
    assert extract_json('{"matched_plants": ["Алоэ", "Хавортия",]}') == {
        "matched_plants": ["Алоэ", "Хавортия"]
    }


def test_extract_json_finds_object_amid_extra_text():
    """Некоторые reasoning-модели добавляют пояснение вокруг JSON, даже
    когда их явно просят этого не делать."""
    content = 'Вот результат:\n{"action": "list"}\nНадеюсь, помогло!'
    assert extract_json(content) == {"action": "list"}


def test_extract_json_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        extract_json("это вообще не JSON и не похоже на него")


# --- _parse_retry_after -----------------------------------------------------


def test_parse_retry_after_extracts_seconds():
    assert _parse_retry_after("Rate limit reached. Please try again in 16.1475s.") == 16.1475


def test_parse_retry_after_case_insensitive():
    assert _parse_retry_after("please TRY AGAIN IN 3s") == 3.0


def test_parse_retry_after_falls_back_to_default_when_no_match():
    assert _parse_retry_after("какая-то другая ошибка без указания времени") == 5.0
