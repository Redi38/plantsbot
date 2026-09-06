import bot.services.ai_service.cache as cache_module
from bot.services.ai_service.cache import cache_get, cache_key, cache_set


def test_cache_roundtrip():
    key = cache_key(1, "добавь алоэ", ["Суккуленты"], ["Хавортия"])
    assert cache_get(key) is None
    cache_set(key, {"action": "add"})
    assert cache_get(key) == {"action": "add"}


def test_cache_key_normalizes_text_case_and_whitespace():
    k1 = cache_key(1, "  Добавь Алоэ  ", None, None)
    k2 = cache_key(1, "добавь алоэ", None, None)
    assert k1 == k2


def test_cache_key_differs_by_user():
    k1 = cache_key(1, "добавь алоэ", None, None)
    k2 = cache_key(2, "добавь алоэ", None, None)
    assert k1 != k2


def test_cache_key_differs_by_existing_groups_or_plants():
    base = cache_key(1, "текст", ["A"], ["B"])
    other_groups = cache_key(1, "текст", ["C"], ["B"])
    other_plants = cache_key(1, "текст", ["A"], ["D"])
    assert base != other_groups
    assert base != other_plants


def test_cache_expires_after_ttl(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: fake_time[0])

    key = cache_key(1, "добавь алоэ", None, None)
    cache_set(key, {"action": "add"})
    assert cache_get(key) == {"action": "add"}

    fake_time[0] += cache_module._CACHE_TTL_SECONDS + 1
    assert cache_get(key) is None


def test_cache_evicts_oldest_when_over_capacity(monkeypatch):
    monkeypatch.setattr(cache_module, "_CACHE_MAX_ENTRIES", 2)
    cache_module._intent_cache.clear()

    cache_set(cache_key(1, "первый", None, None), {"n": 1})
    cache_set(cache_key(1, "второй", None, None), {"n": 2})
    cache_set(cache_key(1, "третий", None, None), {"n": 3})

    assert cache_get(cache_key(1, "первый", None, None)) is None
    assert cache_get(cache_key(1, "второй", None, None)) == {"n": 2}
    assert cache_get(cache_key(1, "третий", None, None)) == {"n": 3}
