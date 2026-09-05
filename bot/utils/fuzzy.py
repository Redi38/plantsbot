"""Общий нечёткий поиск по названию — используется и для групп (см.
bot/db/crud.py), и для растений (см. bot/handlers/ai_agent/delete_flow.py).

Раньше обе сущности искались похожим, но независимо продублированным
кодом (точное совпадение -> подстрока -> difflib), который со временем
рисковал разойтись по поведению. Здесь — единая реализация, параметризуемая
только тем, как достать имя из элемента списка.
"""

import difflib
from typing import Callable, TypeVar

T = TypeVar("T")

DEFAULT_FUZZY_CUTOFF = 0.72


def fuzzy_find(
    items: list[T],
    query: str,
    *,
    key: Callable[[T], str] = lambda item: item.name,
    fuzzy_cutoff: float = DEFAULT_FUZZY_CUTOFF,
) -> list[T]:
    """Ищет элементы, чьё название (key(item)) соответствует query, по
    убывающей строгости:

    1) точное совпадение (без учёта регистра/пробелов);
    2) вхождение подстроки в обе стороны (query в название ИЛИ название в query);
    3) приблизительное совпадение по difflib (опечатки в пределах одного
       алфавита — не помогает с разными языками/транслитерацией, это уже
       задача модели, а не этой функции).

    Возвращает пустой список, если query пуст или совпадений не нашлось,
    иначе — все элементы, подошедшие на первом сработавшем уровне (не
    смешивает уровни между собой)."""
    normalized_query = query.strip().lower()
    if not normalized_query:
        return []

    def normalized(item: T) -> str:
        return key(item).strip().lower()

    exact = [item for item in items if normalized(item) == normalized_query]
    if exact:
        return exact

    substring = [
        item for item in items if normalized_query in normalized(item) or normalized(item) in normalized_query
    ]
    if substring:
        return substring

    names_lower = [normalized(item) for item in items]
    close = set(difflib.get_close_matches(normalized_query, names_lower, n=5, cutoff=fuzzy_cutoff))
    if not close:
        return []
    return [item for item in items if normalized(item) in close]
