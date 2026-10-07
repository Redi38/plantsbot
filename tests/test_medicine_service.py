from datetime import date

import pytest

from bot.db import crud
from bot.services import medicine_service as ms

TODAY = date(2026, 10, 6)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("05.2027", date(2027, 5, 31)),
        ("5/2027", date(2027, 5, 31)),
        ("2027-05", date(2027, 5, 31)),
        ("02.2028", date(2028, 2, 29)),  # високосный год
        ("31.05.2027", date(2027, 5, 31)),
        ("1.6.2027", date(2027, 6, 1)),
        ("2027-05-15", date(2027, 5, 15)),
        ("  05.2027  ", date(2027, 5, 31)),
    ],
)
def test_parse_expiry_accepts(text, expected):
    assert ms.parse_expiry(text) == expected


@pytest.mark.parametrize("text", ["", "скоро", "13.2027", "00.2027", "31.04.2027", "05.27", "05.1999", "05.2101", "5"])
def test_parse_expiry_rejects(text):
    assert ms.parse_expiry(text) is None


def test_format_expiry_month_or_full_date():
    assert ms.format_expiry(date(2027, 5, 31)) == "05.2027"
    assert ms.format_expiry(date(2027, 5, 15)) == "15.05.2027"


async def test_only_name_and_kind_are_required(session, user_id):
    medicine = await ms.add_medicine(session, user_id, "  Актара ", " Инсектицид ")

    assert (medicine.name, medicine.kind) == ("Актара", "Инсектицид")
    assert medicine.active_substance is None
    assert medicine.expires_at is None
    assert medicine.comment is None


async def test_optional_fields_are_stored_and_blank_becomes_none(session, user_id):
    full = await ms.add_medicine(
        session, user_id, "Актара", "Инсектицид", "тиаметоксам", date(2027, 5, 31), "от тли"
    )
    blank = await ms.add_medicine(session, user_id, "Фундазол", "Фунгицид", "   ", None, "")

    assert (full.active_substance, full.expires_at, full.comment) == ("тиаметоксам", date(2027, 5, 31), "от тли")
    assert (blank.active_substance, blank.comment) == (None, None)


@pytest.mark.parametrize(("name", "kind"), [("", "Инсектицид"), ("   ", "Инсектицид"), ("Актара", ""), ("x" * 151, "Инсектицид"), ("Актара", "x" * 51)])
async def test_name_and_kind_are_validated(session, user_id, name, kind):
    with pytest.raises(ValueError):
        await ms.add_medicine(session, user_id, name, kind)


async def test_same_name_can_be_added_twice(session, user_id):
    await ms.add_medicine(session, user_id, "Фитоверм", "Инсектицид", expires_at=date(2027, 1, 31))
    await ms.add_medicine(session, user_id, "Фитоверм", "Инсектицид", expires_at=date(2028, 1, 31))

    assert len(await crud.list_medicines(session, user_id)) == 2


async def test_limit_per_user(session, user_id, monkeypatch):
    monkeypatch.setattr(ms, "MAX_MEDICINES", 2)
    await ms.add_medicine(session, user_id, "A", "Инсектицид")
    await ms.add_medicine(session, user_id, "B", "Инсектицид")

    with pytest.raises(ms.TooManyMedicines):
        await ms.add_medicine(session, user_id, "C", "Инсектицид")


async def test_list_sorted_by_expiry_without_date_last(session, user_id):
    await ms.add_medicine(session, user_id, "Без срока", "Биопрепарат")
    await ms.add_medicine(session, user_id, "Поздно", "Биопрепарат", expires_at=date(2028, 1, 31))
    await ms.add_medicine(session, user_id, "Рано", "Биопрепарат", expires_at=date(2026, 12, 31))

    names = [m.name for m in await crud.list_medicines(session, user_id)]

    assert names == ["Рано", "Поздно", "Без срока"]


async def test_remove(session, user_id):
    medicine = await ms.add_medicine(session, user_id, "Фундазол", "Фунгицид")

    await ms.remove(session, medicine)

    assert await crud.get_medicine(session, medicine.id, user_id) is None


async def test_medicines_are_scoped_to_their_owner(session, user_id):
    other = await crud.get_or_create_user(session, telegram_id=555, username=None, full_name=None)
    await session.commit()
    medicine = await ms.add_medicine(session, user_id, "Фундазол", "Фунгицид")

    assert await crud.get_medicine(session, medicine.id, other.id) is None


def test_reminder_text_variants():
    class M:
        name = "Актара <b>"
        kind = "Инсектицид"

    soon = M()
    soon.expires_at = date(2026, 10, 31)
    today = M()
    today.expires_at = TODAY
    expired = M()
    expired.expires_at = date(2026, 9, 30)

    assert "осталось 25 дн." in ms.render_reminder(soon, TODAY)
    assert "&lt;b&gt;" in ms.render_reminder(soon, TODAY)  # название экранируется
    assert "заканчивается сегодня" in ms.render_reminder(today, TODAY)
    assert "истёк" in ms.render_reminder(expired, TODAY)


async def test_card_hides_empty_optional_fields(session, user_id):
    bare = await ms.add_medicine(session, user_id, "Фундазол", "Фунгицид")
    full = await ms.add_medicine(session, user_id, "Актара", "Инсектицид", "тиаметоксам", date(2027, 5, 31), "от трипсов")

    bare_card = ms.render_card(bare, TODAY)
    full_card = ms.render_card(full, TODAY)

    assert "Действующее вещество" not in bare_card
    assert "Комментарий" not in bare_card
    assert "Срок годности: не указан" in bare_card
    assert "Действующее вещество: тиаметоксам" in full_card
    assert "Срок годности: до 05.2027" in full_card
    assert "Комментарий: от трипсов" in full_card
