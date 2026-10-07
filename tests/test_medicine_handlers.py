"""Сквозные проверки диалога аптечки на том же стенде (настоящий Dispatcher +
фейковый Telegram), что и тесты полива."""

from datetime import date

from bot.db import crud, database
from bot.keyboards.reply import BTN_MEDS
from bot.services import medicine_service
from tests.test_watering_handlers import TG_ID, app


async def _medicines():
    async with database.get_session() as session:
        user = await crud.get_or_create_user(session, TG_ID, None, None)
        return await crud.list_medicines(session, user.id)


async def test_meds_button_shows_empty_menu(app):  # noqa: F811
    await app.say(BTN_MEDS)

    assert "Пока пусто" in app.tg.last_visible_text()
    assert "medadd" in app.tg.last_markup_callbacks()


async def test_full_add_flow_with_all_fields(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    assert "Как называется препарат" in app.tg.last_visible_text()

    await app.say("Актара")
    assert "Какой тип" in app.tg.last_visible_text()

    await app.press("medkind:0")
    assert "Действующее вещество" in app.tg.last_visible_text()

    await app.say("тиаметоксам")
    assert "До какого времени годен" in app.tg.last_visible_text()

    await app.say("05.2099")
    assert "Комментарий" in app.tg.last_visible_text()

    await app.say("от тли")
    assert "✅ «Актара» добавлен в аптечку" in app.tg.last_visible_text()

    (medicine,) = await _medicines()
    assert (medicine.name, medicine.kind, medicine.active_substance) == ("Актара", "Инсектицид", "тиаметоксам")
    assert medicine.expires_at == date(2099, 5, 31)
    assert medicine.comment == "от тли"


async def test_only_name_and_kind_needed(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say("Фундазол")
    await app.say("Раствор для полива")  # свой тип текстом
    await app.press("medskip")  # вещество
    await app.press("medskip")  # срок
    await app.press("medskip")  # комментарий

    assert "✅ «Фундазол» добавлен в аптечку" in app.tg.last_visible_text()
    assert "напоминаний не будет" in app.tg.last_visible_text()
    (medicine,) = await _medicines()
    assert (medicine.kind, medicine.active_substance, medicine.expires_at, medicine.comment) == (
        "Раствор для полива",
        None,
        None,
        None,
    )


async def test_invalid_expiry_is_rejected_and_dialog_continues(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say("Фундазол")
    await app.press("medkind:3")
    await app.press("medskip")

    await app.say("когда-нибудь")
    assert "Не разобрала дату" in app.tg.last_visible_text()
    assert await _medicines() == []

    await app.say("31.12.2099")
    await app.press("medskip")
    (medicine,) = await _medicines()
    assert medicine.expires_at == date(2099, 12, 31)


async def test_cancel_stops_dialog_without_saving(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say("Фундазол")
    await app.press("medcancel")

    assert "Пока пусто" in app.tg.last_visible_text()
    assert await _medicines() == []


async def test_card_and_delete(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say("Фундазол")
    await app.press("medkind:3")
    await app.press("medskip")
    await app.press("medskip")
    await app.press("medskip")
    (medicine,) = await _medicines()

    await app.press(f"med:{medicine.id}")
    assert "Фундазол" in app.tg.last_visible_text()
    assert "Тип: Биопрепарат" in app.tg.last_visible_text()
    assert f"meddel:{medicine.id}" in app.tg.last_markup_callbacks()

    await app.press(f"meddel:{medicine.id}")
    assert "Удалить «Фундазол»" in app.tg.last_visible_text()

    await app.press(f"meddelc:{medicine.id}")
    assert "удалён из аптечки" in app.tg.last_visible_text()
    assert await _medicines() == []


async def test_reminder_buttons(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say("Фундазол")
    await app.press("medkind:3")
    await app.press("medskip")
    await app.press("medskip")
    await app.press("medskip")
    (medicine,) = await _medicines()

    await app.press("medok")
    assert len(await _medicines()) == 1  # «Понятно» ничего не удаляет

    await app.press(f"medtrash:{medicine.id}")
    assert "убран из аптечки" in app.tg.last_visible_text()
    assert await _medicines() == []

    await app.press(f"medtrash:{medicine.id}")  # повторное нажатие не падает
    assert "не найден" in app.tg.last_visible_text()


async def test_menu_button_during_dialog_does_not_become_a_field_value(app):  # noqa: F811
    await app.say(BTN_MEDS)
    await app.press("medadd")
    await app.say(BTN_MEDS)  # повторное нажатие кнопки меню посреди диалога

    assert "Пока пусто" in app.tg.last_visible_text()
    assert await _medicines() == []


async def _add(name, kind, substance=None, expires=None, comment=None):
    async with database.get_session() as session:
        user = await crud.get_or_create_user(session, TG_ID, None, None)
        return await medicine_service.add_medicine(session, user.id, name, kind, substance, expires, comment)


async def test_menu_shows_general_list_with_kind_buttons_and_add_below(app):  # noqa: F811
    await _add("Актара", "Инсектицид")
    await _add("Фитоверм", "Инсектицид")
    await _add("Топаз", "Фунгицид")

    await app.say(BTN_MEDS)

    callbacks = app.tg.last_markup_callbacks()
    assert medicine_service.kind_token("Инсектицид") in "".join(callbacks)
    assert "mg:all" not in callbacks  # кнопки «Показать все» больше нет
    assert not any(c.startswith("med:") for c in callbacks)  # препараты больше не кнопки
    # общая таблица со всеми типами сразу на главном экране
    text = app.tg.last_visible_text()
    assert "Вся аптечка (3)" in text
    assert "Актара" in text and "Фитоверм" in text and "Топаз" in text
    assert "<pre>" not in text  # обычный текст, а не моноширинный код
    # «Добавить» стоит под кнопками всех типов
    assert callbacks.index("medadd") > max(i for i, c in enumerate(callbacks) if c.startswith("mg:"))


async def test_kind_button_opens_table_with_all_fields(app):  # noqa: F811
    await _add("Актара", "Инсектицид", "тиаметоксам", date(2099, 5, 31), "от тли")
    await _add("Топаз", "Фунгицид")

    await app.say(BTN_MEDS)
    await app.press(f"mg:{medicine_service.kind_token('Инсектицид')}")

    text = app.tg.last_visible_text()
    assert "Инсектицид (1)" in text
    assert "Актара" in text and "тиаметоксам" in text and "05.2099" in text and "от тли" in text
    assert "Топаз" not in text
    callbacks = app.tg.last_markup_callbacks()
    assert "medadd" in callbacks and "medmenu" in callbacks


async def test_kind_table_back_returns_to_general_table(app):  # noqa: F811
    await _add("Актара", "Инсектицид")
    await _add("Топаз", "Фунгицид")

    await app.say(BTN_MEDS)
    await app.press(f"mg:{medicine_service.kind_token('Фунгицид')}")
    assert "Топаз" in app.tg.last_visible_text()
    assert "Актара" not in app.tg.last_visible_text()

    await app.press("medmenu")
    assert "Вся аптечка (2)" in app.tg.last_visible_text()
    assert "Актара" in app.tg.last_visible_text() and "Топаз" in app.tg.last_visible_text()


async def test_delete_from_table_via_pick_screen(app):  # noqa: F811
    medicine = await _add("Актара", "Инсектицид")
    await _add("Топаз", "Фунгицид")
    token = medicine_service.kind_token("Инсектицид")

    await app.say(BTN_MEDS)
    await app.press(f"mg:{token}")
    await app.press(f"medpick:{token}")
    assert "Какой препарат" in app.tg.last_visible_text()
    assert app.tg.last_markup_callbacks().count(f"meddel:{medicine.id}") == 1

    await app.press(f"meddel:{medicine.id}")
    await app.press(f"meddelc:{medicine.id}")

    assert [m.name for m in await _medicines()] == ["Топаз"]
    assert "Вся аптечка (1)" in app.tg.last_visible_text()  # вернулись к общей таблице


async def test_stale_kind_button_returns_to_menu(app):  # noqa: F811
    medicine = await _add("Актара", "Инсектицид")
    token = medicine_service.kind_token("Инсектицид")
    await app.say(BTN_MEDS)
    await app.press(f"meddel:{medicine.id}")
    await app.press(f"meddelc:{medicine.id}")

    await app.press(f"mg:{token}")  # кнопка со старого сообщения

    assert "уже пуст или удалён" in app.tg.last_visible_text()
