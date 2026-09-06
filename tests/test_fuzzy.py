from dataclasses import dataclass

from bot.utils.fuzzy import fuzzy_find


@dataclass
class Item:
    name: str


def test_exact_match_wins_and_is_case_and_space_insensitive():
    items = [Item("Алоказия Полли"), Item("Алоказия Одора")]
    result = fuzzy_find(items, "  алоказия полли  ")
    assert [i.name for i in result] == ["Алоказия Полли"]


def test_exact_match_short_circuits_substring_candidates():
    """Если есть точное совпадение, подстрочные кандидаты не подмешиваются —
    уровни не смешиваются между собой."""
    items = [Item("Алоэ"), Item("Алоэ Вера")]
    result = fuzzy_find(items, "алоэ")
    assert [i.name for i in result] == ["Алоэ"]


def test_substring_match_both_directions():
    items = [Item("Алоказия Полли"), Item("Хавортия")]
    # query — подстрока названия
    assert [i.name for i in fuzzy_find(items, "полли")] == ["Алоказия Полли"]
    # название — подстрока query (например, пользователь дописал лишнее слово)
    items2 = [Item("Алоэ")]
    assert [i.name for i in fuzzy_find(items2, "алоэ вера домашняя")] == ["Алоэ"]


def test_fuzzy_typo_match_via_difflib():
    items = [Item("Хавортия")]
    result = fuzzy_find(items, "Хаворция")  # одна опечатка
    assert [i.name for i in result] == ["Хавортия"]


def test_empty_query_returns_empty():
    items = [Item("Алоэ")]
    assert fuzzy_find(items, "") == []
    assert fuzzy_find(items, "   ") == []


def test_no_match_returns_empty():
    items = [Item("Алоэ"), Item("Хавортия")]
    assert fuzzy_find(items, "совершенно другое растение") == []


def test_custom_key_function():
    """Используется, например, в plant_service.find_plants_by_term — ключ
    там не полное имя, а первое слово (род)."""
    items = [Item("Алоказия Полли"), Item("Алоказия Одора"), Item("Хавортия")]
    result = fuzzy_find(items, "алоказия", key=lambda i: i.name.split()[0])
    assert {i.name for i in result} == {"Алоказия Полли", "Алоказия Одора"}


def test_custom_cutoff_is_respected():
    items = [Item("Хавортия")]
    # запрос слишком далёкий по буквам — при высоком cutoff совпадения нет
    assert fuzzy_find(items, "Совсем другое", fuzzy_cutoff=0.95) == []
